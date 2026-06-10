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
import { Chat } from "./Chat";
import { LayerPanel, createLayerPanelBus } from "./LayerPanel";
import { LayerLegend } from "./components/LayerLegend";
import {
  AuthGate,
  clearAnonymousAccepted,
  readAnonymousAccepted,
} from "./components/AuthGate";
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
  signInWithGoogle,
} from "./auth";
import { GraceWs } from "./ws";
import { SourceCandidatePayload } from "./lib/source_suggestion_suppression";
import { useCases } from "./hooks/useCases";
import { useSaveGate } from "./hooks/useSaveGate";
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

// WebSocket endpoint — local agent (job-0015) defaults to ws://localhost:8765.
// Override at build time with VITE_GRACE2_WS_URL.
const WS_URL: string =
  (import.meta.env.VITE_GRACE2_WS_URL as string | undefined) ??
  "ws://localhost:8765";

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
const hamburgerBtnStyle: React.CSSProperties = {
  position: "absolute",
  background: "rgba(20,20,25,0.85)",
  border: "1px solid #444",
  borderRadius: 6,
  color: "#ccc",
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

  // Collapse toggles — initialised from localStorage so reloads remember
  // the user's preference.
  const [leftCollapsed, setLeftCollapsed] = useState<boolean>(() =>
    readCollapsed(LS_LEFT_COLLAPSED),
  );
  const [rightCollapsed, setRightCollapsed] = useState<boolean>(() =>
    readCollapsed(LS_RIGHT_COLLAPSED),
  );

  // Layers lifted here from session-state so:
  //   (a) LayerLegend can read the list
  //   (b) we can gate the left panel conditional mount on layers.length > 0
  const [layers, setLayers] = useState<ProjectLayerSummary[]>([]);

  // Map theme (job-0076).
  const [theme, setTheme] = useState<MapTheme>(() => readTheme());

  // Auth state (job-0123, sprint-12-mega Wave 2).
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authResolved, setAuthResolved] = useState<boolean>(false);
  useEffect(() => {
    const unsub = onAuthChanged((u) => {
      setAuthUser(u);
      setAuthResolved(true);
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

  // Sign-in handler routed through Settings + SaveGate.
  const handleSignInRequest = useCallback(() => {
    void (async () => {
      try {
        await signInWithGoogle();
      } catch {
        // Sign-in errors surface via Firebase's own UI; nothing to do.
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
  const onDeleteGated = useCallback(
    (caseId: string) => {
      saveGate.gateAction(
        () => deleteCase(caseId),
        "Delete Case",
      )();
    },
    [saveGate, deleteCase],
  );

  // currentCaseId for the SecretsPopup scope.
  const currentCaseId: string | null = activeCaseId;

  // job-0127: payload-warning gates.
  const [payloadWarnings, setPayloadWarnings] = useState<
    PayloadWarningEnvelopePayload[]
  >([]);
  const handlePayloadWarningDecide = useCallback(
    (
      warningId: string,
      decision: PayloadConfirmationDecision,
      revised: Record<string, unknown> | null,
    ) => {
      wsRef.current?.sendPayloadConfirmation(warningId, decision, revised);
      setPayloadWarnings((prev) =>
        prev.filter((w) => w.warning_id !== warningId),
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
      onPayloadWarning: (p) => setPayloadWarnings((prev) => [p, ...prev]),
      onCaseList: (p: CaseListEnvelopePayload) => useCases_onCaseList(p),
      onCaseOpen: (p: CaseOpenEnvelopePayload) => useCases_onCaseOpen(p),
      onError: () => { /* Chat owns rendering */ },
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
  }, [bus, fanoutSourceSuggestion, useCases_onCaseList, useCases_onCaseOpen, handleChartEmission]);

  // job-0137: Case rehydration replay.
  useEffect(() => {
    // sprint-13 job-0231: Case switch resets charts (replace-not-reconcile
    // client-side rule). Charts for the new Case replay via
    // activeSession.charts below; on null (no active Case) we clear.
    setCharts([]);

    if (activeSession === null) {
      bus.pushSessionState({
        loaded_layers: [],
        chat_history: [],
        pipeline_history: [],
        current_pipeline: null,
        map_view: null,
      });
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
    window.__grace2InjectPayloadWarning = (p) =>
      setPayloadWarnings((prev) => [p, ...prev]);
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
  }, [bus, fanoutSourceSuggestion, useCases_onCaseList, useCases_onCaseOpen, handleChartEmission]);

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

  // job-0138: AuthGate full-screen gating.
  if (!appShouldRender) {
    return (
      <AuthGate
        onAnonymousAccept={handleAnonymousAccept}
      />
    );
  }

  // job-0143: derive the active Case object for the breadcrumb title.
  const activeCase = activeCaseId
    ? cases.find((c) => c.case_id === activeCaseId) ?? null
    : null;

  return (
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

      {/* LayerLegend — bottom-center absolute; z-index 10. */}
      <LayerLegend layers={layers} />

      {/* Left rail (job-0143):
            - No active Case → CasesPanel only (list view).
            - Active Case → CaseView (breadcrumb + LayerPanel children). */}
      {!leftCollapsed && activeCaseId === null && (
        <div
          data-testid="grace2-left-rail"
          data-mode="cases-list"
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
      {!leftCollapsed && activeCaseId !== null && (
        <>
          {/* Breadcrumb at the canonical top-left position. z-index 22 so
              it sits ABOVE the LayerPanel wrapper (z=20) — the panel is
              repositioned below the breadcrumb via top/left wrapper css. */}
          <div
            data-testid="grace2-left-rail"
            data-mode="case-view"
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
                  width: 280 + 16 + 16, // left:16 offset + 280 panel + 16 right padding
                }}
              >
                <LayerPanel
                  subscribeSessionState={bus.subscribeSessionState}
                  subscribeMapCommand={bus.subscribeMapCommand}
                  initialLayers={layers}
                  onClose={collapseLeft}
                />
              </div>
            </div>
          )}
        </>
      )}

      {/* job-0143: Bottom-row Settings + Secrets pills. Hidden when the
          left rail is collapsed (they belong to the rail). */}
      {!leftCollapsed && (
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
        aria-hidden={rightCollapsed}
        style={{
          display: rightCollapsed ? "none" : "contents",
        }}
      >
        <Chat wsUrl={WS_URL} onClose={collapseRight} />
      </div>

      {/* Layers hamburger — top-LEFT. */}
      {showLayersHamburger && (
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

      {/* Chat hamburger — top-RIGHT. */}
      {showChatHamburger && (
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

      {/* Upgrade toast (job-0138 kickoff item 6). Renders below the chat
          hamburger so it doesn't collide with adjacent UI. */}
      {upgradeToast && (
        <div
          data-testid="grace2-upgrade-toast"
          role="status"
          style={{
            position: "absolute",
            top: 56,
            right: rightCollapsed ? 60 : 380,
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
          right: rightCollapsed ? 16 : 32,
          top: rightCollapsed ? 64 : 70,
          width: 340,
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
          {/* Payload-warning gates (job-0127 → restyled job-0145). Newest
              first so a fresh gate is always at the top of the stack. */}
          {payloadWarnings.map((w) => (
            <PayloadWarningInline
              key={w.warning_id}
              warning={w}
              onDecide={(decision, revised) =>
                handlePayloadWarningDecide(w.warning_id, decision, revised)
              }
            />
          ))}
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
      {/* Legacy data-testid hook (job-0127 → job-0145): keep the
          `payload-warning-stack` test id reachable so existing App / e2e
          tests continue to find the column. Mounted only when at least one
          warning is active, mirroring the prior conditional-render. */}
      {payloadWarnings.length > 0 && (
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
  );
}
