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
//   |   [⚙ Settings]   ← bottom-row pill (Secrets now inside it)|
//   |                       Map (full bleed)                    |
//   |              [LayerLegend anchored to AOI bbox] (Map.tsx) |
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
import { getLayerCache } from "./lib/layer_cache";
// JOB WEB-ANIM (#157.2-.3) — the floating sequence scrubber now lives at the App
// level (not inside LayerPanel) so it renders WHENEVER a sequence is animating on
// the shared AnimationController, regardless of whether the Layers panel is open.
import { SequenceScrubber } from "./components/SequenceScrubber";
import { getAnimationController } from "./lib/animation_controller";
import { useAnimationState } from "./lib/use_animation_controller";
import type { ScreenRect } from "./lib/legend_snap";
import {
  AuthGate,
  clearAnonymousAccepted,
  readAnonymousAccepted,
} from "./components/AuthGate";
import { AuthGuard } from "./components/AuthGuard";
import { CasesPanel } from "./components/CasesPanel";
import { CaseView } from "./components/CaseView";
import { SettingsPopup } from "./components/SettingsPopup";
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
// FIX 2 (NATE 2026-06-17): the large-payload warning moved into Chat's per-Case
// interleaved stream (in-chat card), so App no longer imports / renders
// PayloadWarningInline. See Chat.tsx routePayloadWarning + InterleavedChatStream.
import {
  AuthUser,
  onAuthChanged,
  signOut as authSignOut,
  signIn as authSignIn,
  handleRedirectCallback,
  getIdToken,
} from "./auth";
import { ConnectionStatus, GraceWs } from "./ws";
import { SourceCandidatePayload } from "./lib/source_suggestion_suppression";
import { extractLastZoomTo, asBbox } from "./lib/case_zoom";
import { useCases } from "./hooks/useCases";
import { useIsMobile } from "./hooks/useIsMobile";
import { useSaveGate } from "./hooks/useSaveGate";
import {
  MobileDrawer,
  MobileDrawerButton,
} from "./components/MobileDrawer";
import { IconMenu, IconSettings } from "./components/icons";
import { AgentWaker, wakeConfigured, WakeState } from "./lib/wake";
import { fetchCaseView, caseViewConfigured } from "./lib/case_view";
import { fetchCaseList, caseListConfigured } from "./lib/case_list";
import {
  CaseListEnvelopePayload,
  CaseOpenEnvelopePayload,
  MapCommandPayload,
  // PayloadWarningEnvelopePayload retained for the dev-only window seam typing
  // (FIX 2: the warning is rendered by Chat now, not App).
  PayloadWarningEnvelopePayload,
  PipelineStatePayload,
  ProjectLayerSummary,
  ProviderID,
  SecretRecord,
  SecretsListPayload,
  SessionStatePayload,
} from "./contracts";

// sleep/wake STAGE 2 (NATE 2026-06-18) — number of CONSECUTIVE failed reconnect
// schedules before we run the REPORT-ONLY wakeState() GET probe (which classifies
// asleep and may surface the composer Wake UI). The first failed attempt is
// usually a transient WS blip while the box is still UP (CloudFront idle cull,
// brief network drop) — probing then would be noise. By the SECOND consecutive
// failure the box is plausibly stopped, so we GET-probe its state. NEVER
// AUTO-WAKE: the probe is read-only; only the user's explicit composer tap POSTs
// wake (StartInstances).
const WAKE_OVERLAY_THRESHOLD = 2;

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

  // job-0179 (per-Case client cache + view-state durability — "the seatbelt").
  // The process-global LayerCache holds the per-Case layer SET (in-memory) and
  // the user's per-layer view-overrides (opacity / visibility / zIndex, mirrored
  // to IndexedDB). It is the single source of truth the bus-subscribing surfaces
  // (Map.tsx reconcile teardown gate, LayerPanel user edits) share. A WS blip /
  // stale snapshot routes through cache.mergeSnapshot (additive, never evicts);
  // only an explicit Case switch / delete evicts. See lib/layer_cache.ts.
  const layerCache = useMemo(() => getLayerCache(), []);
  useEffect(() => {
    // Best-effort one-time hydrate of persisted view-overrides from IndexedDB.
    void layerCache.hydrate();
  }, [layerCache]);

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

  // Layers lifted here from session-state so we can gate the left panel
  // conditional mount on layers.length > 0 and feed the LayerPanel. (job-0321
  // F43: the legend itself no longer reads this list at App level — it renders
  // inside Map.tsx anchored to the AOI bounding box.)
  const [layers, setLayers] = useState<ProjectLayerSummary[]>([]);

  // Map theme (job-0076).
  const [theme, setTheme] = useState<MapTheme>(() => readTheme());

  // The TRUE projected AOI screen rectangle, lifted out of MapView so the
  // SequenceScrubber (rendered inside LayerPanel, which has no map handle) can
  // pin bottom-center of the AOI box and track pan/zoom like the legend keys.
  // MapView fires onAoiScreenRectChange when the rect changes (null when there
  // is no AOI / it leaves the viewport). Mirrors the layers/chatWidth lift
  // pattern: App holds the Map-derived screen state, LayerPanel consumes it.
  const [aoiScreenRect, setAoiScreenRect] = useState<ScreenRect | null>(null);

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
  // job-0357 (per-Case layer DURABILITY) — live WS connection status, held in
  // a ref so the GraceWs `onSessionState` handler (a stable closure created
  // once when the socket is constructed) can read the CURRENT status without
  // being re-created on every status flip. The map-side LayerPanel bus push
  // stamps `session-state.replace_layers` from this: server snapshots received
  // while NOT `connected` (the disconnect / reconnect window) are
  // non-authoritative top-ups that must NOT tear down the active Case's
  // already-rendered layers; snapshots received while `connected` are
  // authoritative (live layer add AND delete apply via replace-not-reconcile).
  const wsStatusRef = useRef<ConnectionStatus>("connecting");

  // CASE-SWITCH LAYER LEAK FIX (NATE 2026-06-19) — the currently-active Case id,
  // mirrored into a ref so the GraceWs `onSessionState` handler (a STABLE
  // closure created once when the socket is constructed, see the WS effect
  // below) can read the LIVE active Case without being re-created on every Case
  // switch (which would tear down + re-open the socket — the WS cycling we must
  // avoid). The leak NATE hit: switching Case A -> B paints B's layers, then a
  // TRAILING server `session-state` STILL TAGGED with Case A (a late solve-finish
  // snapshot, or the server's resume replay racing the case-open) arrives over
  // the live socket; under the old handler it was stamped authoritative
  // (`replace_layers:true`, because the socket is `connected`) and Map.tsx
  // REPLACED B's layers with A's. ws.ts already extracts the envelope-level
  // `case_id` and passes it as the 2nd arg of `onSessionState`; we now DROP any
  // snapshot whose tag != the active Case. Synced in the effect just below.
  const activeCaseIdRef = useRef<string | null>(null);

  // sleep/wake STAGE 2 (NATE 2026-06-18) — the always-on agent box can be
  // STOPPED by the idle-check Lambda; a stopped box answers nothing so the WS
  // can't connect. STAGE 2 gates ONLY the chat COMPOSER behind a Connecting ->
  // (Chat | Wake) state machine (Chat.tsx owns the slot); the scrollback + the
  // whole map stay LIVE with the box asleep. App.tsx is the SINGLE SOURCE OF
  // TRUTH for the asleep signal (the App socket + a report-only wakeState GET)
  // and threads it down to Chat:
  //   - `wsStatus` mirrors the App socket's live status as STATE (the ref above
  //     is a stable closure read; the asleep derivation needs a re-render).
  //   - the consecutive-failure count arrives directly as the `attempt` arg of
  //     ws.ts `onWakeNeeded`. We only RUN the report-only wakeState() probe past
  //     WAKE_OVERLAY_THRESHOLD so a single transient blip (one failed attempt)
  //     never trips the Wake UI.
  //   - `agentAsleep` is the classified result of that GET probe (true when the
  //     box reports stopped/stopping). It NEVER triggers a wake — only the
  //     user's explicit composer tap POSTs wake. Cleared on a healthy reconnect.
  //   - `wakerRef` is the SHARED AgentWaker so the composer's explicit-tap path
  //     (resetDebounce -> StartInstances POST) and the report-only GET probe
  //     coalesce against the same instance.
  const [wsStatus, setWsStatus] = useState<ConnectionStatus>("connecting");
  // sleep/wake STAGE 2 — classified asleep signal from the report-only GET
  // probe (true = box reports stopped/stopping). Drives Chat's composer Wake UI.
  // NEVER set from a reconnect/case-open alone (never auto-wake); only the GET
  // probe sets it. A successful WS reconnect clears it (the box is up).
  const [agentAsleep, setAgentAsleep] = useState<boolean>(false);
  const wakerRef = useRef<AgentWaker | null>(null);
  if (wakerRef.current === null) wakerRef.current = new AgentWaker();
  // sleep/wake STAGE 2 — guard so the report-only wakeState() probe runs at most
  // once per "unreachable" episode (not on every reconnect tick). Reset on a
  // healthy reconnect.
  const wakeProbeInFlightRef = useRef<boolean>(false);

  // Settings popup visibility (job-0143). job-0321 F29 — the standalone
  // Secrets popup is retired; API-key management now lives INSIDE Settings
  // (SettingsPopup's embedded SecretsPanel), so there is no separate
  // `secretsOpen` state any more.
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
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

  // currentCaseId for the embedded SecretsPanel scope (inside Settings).
  const currentCaseId: string | null = activeCaseId;

  // FIX 2 (NATE 2026-06-17): the payload-warning gate moved OUT of App into
  // Chat's per-Case interleaved stream (an in-chat card, not a banner "hat").
  // App no longer accumulates / renders / answers the warning — Chat owns the
  // whole flow (route + render + sendPayloadConfirmation) because tool-payload-
  // warning is session-scoped and reaches Chat's GraceWs directly.

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

  // sleep/wake STAGE 2 — whether the composer should surface the Wake UI. App
  // owns this single source of truth (the App socket + the report-only probe)
  // and threads it down to Chat, which renders the Wake UI INSIDE the composer
  // slot only (scrollback + map stay live). Gated on wakeConfigured() so dev/LAN
  // (no wake endpoint -> the box is never auto-stopped) never shows it, and on
  // the App socket NOT being connected (a healthy App socket implies the box is
  // up). The actual asleep classification is `agentAsleep`, set by the GET probe.
  const composerWakeReady =
    wakeConfigured() && wsStatus !== "connected" && agentAsleep;

  // Explicit user tap on the composer's "Wake up agent" rectangle: reset the
  // shared waker's debounce so a manual press always fires StartInstances (even
  // right after a prior attempt) and POST the wake endpoint. This is the ONLY
  // path that POSTs wake (never auto-wake). Fire-and-forget — never throws. The
  // App socket's onStatus "connected" clears agentAsleep when the box is back.
  const handleWakeTap = useCallback(() => {
    const waker = wakerRef.current;
    if (!waker) return;
    waker.resetDebounce();
    void waker.wake().catch(() => {
      /* best-effort; the reconnect loop owns recovery */
    });
  }, []);

  // Mount a GraceWs that routes session-state, map-command, AND secrets-list.
  useEffect(() => {
    const ws = new GraceWs(WS_URL, {
      // job-0357 — record live status so onSessionState can classify a
      // server snapshot as authoritative (connected) vs a reconnect top-up.
      // Auto-stop/wake — ALSO mirror into state so the WakeOverlay re-renders
      // on a flip. On a successful (re)connect, clear the wake state: the box
      // is up, so the overlay fades out and the attempt counter resets.
      onStatus: (s) => {
        wsStatusRef.current = s;
        setWsStatus(s);
        if (s === "connected") {
          // Healthy (re)connect — the box is up. Clear the asleep state (the
          // composer flips Connecting/Wake -> Chat) and reset the probe guard so
          // a future unreachable episode probes again.
          setAgentAsleep(false);
          wakeProbeInFlightRef.current = false;
        }
      },
      // sleep/wake STAGE 2 — ws.ts schedules a reconnect that won't open (the box
      // may be stopped). NEVER AUTO-WAKE: ws.ts no longer POSTs wake here. Track
      // the consecutive-failure count and, once we cross the threshold, run a
      // REPORT-ONLY GET probe (wakeState — never starts the box) to classify
      // asleep. If the box reports stopped/stopping we flip `agentAsleep` so the
      // composer surfaces the tap-to-wake UI; otherwise we keep retrying the WS
      // (the composer stays "Connecting"). The wakeProbeInFlightRef guard runs
      // the probe at most once per unreachable episode (not on every tick).
      onWakeNeeded: (attempt) => {
        if (attempt < WAKE_OVERLAY_THRESHOLD) return;
        if (wakeProbeInFlightRef.current) return;
        const ws = wsRef.current;
        if (!ws) return;
        wakeProbeInFlightRef.current = true;
        void ws
          .reportWakeState()
          .then((state: WakeState) => {
            // Only the App socket's onStatus "connected" clears agentAsleep; a
            // probe that comes back "running"/"pending" leaves it as-is (keep
            // retrying WS). stopped/stopping -> asleep (show Wake UI).
            if (state === "stopped" || state === "stopping") {
              setAgentAsleep(true);
            }
          })
          .catch(() => {
            /* report-only probe is best-effort; stay in Connecting */
          })
          .finally(() => {
            // Allow a re-probe on the NEXT threshold crossing (e.g. a probe that
            // came back "pending" and the box later actually stopped). onStatus
            // "connected" also resets this.
            wakeProbeInFlightRef.current = false;
          });
      },
      onAgentChunk: () => { /* Chat owns rendering */ },
      onPipelineState: () => { /* Chat owns rendering */ },
      // job-0357 (per-Case layer DURABILITY) - stamp the client-only
      // `replace_layers` hint Map.tsx reads to decide replace-not-reconcile
      // (Appendix A.7) vs additive top-up. See the CLIENT FLICKER FIX note on
      // the stamp itself below for the exact authoritative-vs-no-op rule.
      onSessionState: (p, caseId) => {
        // CASE-SWITCH LAYER LEAK FIX (NATE 2026-06-19) — DROP a server snapshot
        // tagged with a Case that is NOT the active one. ws.ts surfaces the
        // envelope-level `case_id` here as `caseId`; a snapshot whose tag does
        // not match `activeCaseIdRef.current` is a TRAILING update for a Case we
        // already left (a late solve-finish frame, or the resume replay racing
        // the new Case's case-open). Applying it to the live map would replace
        // the now-active Case's layers with the stale Case's (NATE's bug:
        // "the layers are filled with the urban flood ones, and the original
        // disappeared"). We compare ONLY when BOTH are non-null: a snapshot with
        // no tag (`caseId == null`) is an untagged/root frame we still honor (it
        // never carries another Case's layers), and an untagged-active state
        // (`activeCaseIdRef.current == null`, the root view) likewise applies —
        // so per-Case layer DURABILITY across a WS reconnect is unaffected (a
        // reconnect resume for the SAME active Case is either tagged with that
        // Case, matching here, or untagged, applied). Only a genuine
        // cross-Case mismatch is dropped.
        const active = activeCaseIdRef.current;
        if (caseId != null && active != null && caseId !== active) return;
        bus.pushSessionState({
          ...p,
          // CLIENT FLICKER FIX (per-Case layer DURABILITY) - a SERVER-DELIVERED
          // snapshot is authoritative (full replace-not-reconcile: live adds AND
          // deletes apply) ONLY when the socket is healthy AND it actually
          // carries layers. The server re-ships a full session-state on every
          // resume INCLUDING the 25s keepalive heartbeat; a heartbeat (or a
          // reconnect mid-flight) can momentarily carry an EMPTY / stale
          // loaded_layers for the SAME Case, which under the old
          // `replace_layers = (connected)` stamp wiped the map then refilled on
          // the next good frame -> the flicker, and violated the durability HARD
          // REQ. An EMPTY server frame is now NON-authoritative (additive no-op):
          // Map.tsx never tears down the active Case's already-rendered overlays
          // on it. The EXPLICIT Case SWITCH / EXIT path (the activeSession effect
          // below) still stamps replace_layers:true on its empty clear, so only a
          // real Case change clears prior-Case layers.
          replace_layers:
            wsStatusRef.current === "connected" &&
            (p.loaded_layers?.length ?? 0) > 0,
        });
      },
      onMapCommand: (p) => bus.pushMapCommand(p),
      onSecretsList: (p) => setSecrets(p.secrets ?? []),
      onMode2Candidate: (p) => fanoutSourceSuggestion(p),
      // FIX 2 — payload-warning is handled by Chat's GraceWs now (in-chat card),
      // not App. No onPayloadWarning handler here.
      onCaseList: (p: CaseListEnvelopePayload) => useCases_onCaseList(p),
      onCaseOpen: (p: CaseOpenEnvelopePayload) => {
        // CASE-SWITCH LAYER LEAK FIX (NATE 2026-06-19) — DROP a trailing
        // `case-open` for a Case we already LEFT. The same leak class as the
        // session-state guard above: after switching A -> B, a late `case-open`
        // still tagged with Case A (an in-flight `select` reply racing the new
        // one) would re-assert A's whole session — rail, layers AND chat — over
        // B. We drop ONLY when the active Case is non-null AND the incoming
        // Case is a DIFFERENT non-null Case. This deliberately still applies:
        //   - auto-create from root (active == null) — a brand-new Case opens;
        //   - the normal `select(B)` reply (incoming B == active B, set
        //     optimistically by selectCase) — re-affirms B idempotently;
        //   - a deselect-to-root reply (incoming null) — clears cleanly.
        const incoming = p.session_state?.case.case_id ?? null;
        const active = activeCaseIdRef.current;
        if (incoming != null && active != null && incoming !== active) return;
        useCases_onCaseOpen(p);
        // job-0179 — mirror the cold-load: push case-open onto the bus so Chat
        // can build the stream's chat-history bubbles. Idempotent (routeCaseOpen
        // only rebuilds a stream the first time it sees the caseId).
        bus.pushCaseOpen(p);
      },
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
    }, { waker: wakerRef.current ?? undefined });
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
    //
    // BUG 4a (Wave 4.9) STABILITY CONTRACT — every dep here is a STABLE
    // reference so an UNRELATED re-render does NOT tear down + re-open the
    // GraceWs (which presented as the ~10-45s WS cycling). Specifically:
    //   - bus: useMemo([], ...) — created once.
    //   - fanoutSourceSuggestion / handleChartEmission: useCallback([], ...).
    //   - useCases_onCaseList / useCases_onCaseOpen: useCallback([], ...) inside
    //     useCases (verified stable in hooks/useCases.ts).
    //   - authEpoch: a number that ONLY changes on a re-sign-in recovery.
    // Do NOT add an unmemoized object/closure to this array — it would recreate
    // the socket every render. (Tested in App.test.tsx "GraceWs creation effect
    // stability".)
  }, [bus, fanoutSourceSuggestion, useCases_onCaseList, useCases_onCaseOpen, handleChartEmission, authEpoch]);

  // LANE CASE-WEB — keep the GraceWs's notion of the CURRENT active Case in
  // sync with useCases.activeCaseId. ws.ts STAMPS this onto every outbound
  // user-message + session-resume so the server treats the client as the case
  // authority. This is a SEPARATE effect from the socket-construction effect on
  // purpose: that effect's deps are deliberately all-stable (adding activeCaseId
  // there would tear down + re-open the socket on every Case switch — the WS
  // cycling we must avoid). Here we only push the value into the EXISTING
  // socket. The null-guard covers the brief construct/teardown window; the open
  // handler reads the latest currentCaseId at connect time regardless.
  useEffect(() => {
    wsRef.current?.setCurrentCaseId(activeCaseId);
    // CASE-SWITCH LAYER LEAK FIX — keep the ref the stable onSessionState
    // closure reads in lockstep with the active Case so a trailing snapshot
    // tagged with the PREVIOUS Case is dropped the instant we switch.
    const prevCaseId = activeCaseIdRef.current;
    activeCaseIdRef.current = activeCaseId;
    // job-0179 — keep the shared LayerCache's notion of the active Case in
    // lockstep so Map.tsx / LayerPanel (bus subscribers with no caseId prop)
    // resolve allowsEvict / getOverride against the right Case. A genuine Case
    // SWITCH (prev != next, both meaningful) is the ONLY in-memory evict path:
    // drop the Case we just LEFT so its layer SET no longer protects against the
    // new Case's authoritative replace. (The persisted view-overrides survive
    // the evict — re-opening the old Case restores them.) Snapshot omission never
    // reaches here, so the seatbelt holds.
    if (prevCaseId !== null && prevCaseId !== activeCaseId) {
      layerCache.evictCase(prevCaseId);
    }
    layerCache.activeCaseId = activeCaseId;
  }, [activeCaseId, layerCache]);

  // job-0322 F31 — resume-repaint (iOS zombie-socket fix). Mobile browsers
  // tear down (or silently wedge) the WebSocket when the tab is backgrounded;
  // on return the in-memory layers were never re-pulled, so the map looks empty
  // until a Case reopen.
  //
  // On `visibilitychange → visible`:
  //   - MOBILE: iOS Safari leaves the socket nominally `OPEN` while the
  //     underlying connection is dead, so the lighter reconnect() path no-ops
  //     and requestSessionState() sends `session-resume` into a dead socket
  //     (the server never re-emits session-state). We call forceReconnect()
  //     which UNCONDITIONALLY tears the socket down and re-opens; the fresh
  //     open handler re-sends auth-token + session-resume, so the layers
  //     reconcile back through replace-not-reconcile (Appendix A.7). No
  //     separate requestSessionState() — the open handler resumes for us.
  //   - DESKTOP: the socket reliably fires `close` when it actually drops, so
  //     the cheaper reconnect() (revive only if dropped) + requestSessionState()
  //     (re-pull on the live socket) is enough and avoids needlessly dropping a
  //     healthy connection. Both are idempotent.
  //
  // The wsRef null-guard covers the brief window between unmount and re-mount.
  useEffect(() => {
    const onVisibility = (): void => {
      if (document.visibilityState !== "visible") return;
      const ws = wsRef.current;
      if (!ws) return;
      // BUG 4a (Wave 4.9) — do NOT force-reconnect an already-OPEN socket. A
      // healthy live connection only needs a state re-pull on resume; tearing
      // it down churns the socket (the cycling this fix targets). Only a
      // closed/closing/never-connected socket gets the teardown path:
      //   - OPEN: lighter requestSessionState() — re-pull authoritative
      //     session-state on the live socket (no teardown). The keepalive's
      //     missed-pong detector now owns the iOS zombie case (a dead socket
      //     that still reports OPEN) instead of an unconditional resume-time
      //     teardown, so this is safe on mobile too.
      //   - NOT OPEN (mobile background tear-down / desktop drop): revive it.
      //     forceReconnect() (mobile) / reconnect() (desktop) re-opens; the
      //     fresh open handler re-sends auth-token + session-resume, so the
      //     layers reconcile back via replace-not-reconcile (Appendix A.7).
      if (ws.isOpen) {
        ws.requestSessionState();
        return;
      }
      if (isMobile) {
        // Not OPEN: unconditionally re-open. The fresh open handler re-sends
        // session-resume itself, so no separate requestSessionState().
        ws.forceReconnect();
        return;
      }
      // Desktop, not OPEN: revive first (dead socket), then pull state.
      ws.reconnect();
      ws.requestSessionState();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [isMobile]);

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
      // job-0357: Case EXIT is an AUTHORITATIVE clear — replace_layers:true so
      // Map.tsx tears down the prior Case's overlays (fresh slate). This is the
      // explicit Case-switch path the durability fix must KEEP clearing; only a
      // WS reconnect (server snapshot received while not `connected`) is exempt.
      bus.pushSessionState({
        loaded_layers: [],
        chat_history: [],
        pipeline_history: [],
        current_pipeline: null,
        map_view: null,
        replace_layers: true,
      });
      // ux-batch-1 (F14): exiting a Case must reset client map state, not just
      // the panels. The analysis-extent (AOI) rectangle is drawn directly on
      // the map by Map.tsx and is NOT part of loaded_layers, so clearing
      // session-state above does not remove it. Emit an explicit clear so the
      // prior Case's AOI outline does not linger on the root/new-case map.
      bus.pushMapCommand({
        command: "clear-analysis-extent",
      } as unknown as MapCommandPayload);
      // ux-batch-1 (F-CASES-CLEAR-ALL): also snap the camera back to the
      // default CONUS view so leaving a Case visibly resets the map (the empty
      // session-state above clears the data layers; this resets the camera).
      bus.pushMapCommand({
        command: "reset-view",
      } as unknown as MapCommandPayload);
      return;
    }
    // job-0357: opening / switching INTO a Case is an AUTHORITATIVE replace —
    // replace_layers:true so the new Case's loaded_layers replace whatever the
    // previously-viewed Case had on the map (a Case switch still clears, per
    // the durability requirement). The reconnect exemption only applies to
    // server-delivered snapshots received while the socket is not `connected`.
    bus.pushSessionState({
      loaded_layers: activeSession.loaded_layers ?? [],
      chat_history: activeSession.chat_history ?? [],
      pipeline_history: activeSession.pipeline_history ?? [],
      current_pipeline: activeSession.current_pipeline ?? null,
      map_view: null,
      replace_layers: true,
    });
    // JOB WEB-AOI-LEGEND (#159) — snap to the FINAL/floored AOI. Prefer
    // `case.bbox` (the agent-AOI job now persists the FLOORED bbox there), but
    // validate it with the SAME finite-4-number guard the zoom-to replay path
    // uses (asBbox) — a null / malformed / non-finite persisted bbox must NOT
    // produce a broken fitBounds; it falls through to the last zoom-to instead.
    const caseBbox = asBbox(activeSession.case.bbox);
    if (caseBbox) {
      bus.pushMapCommand({
        command: "zoom-to",
        args: { bbox: caseBbox },
      } as unknown as MapCommandPayload);
    } else {
      // job-0280 — Case-open snap-to-location. `CaseSummary.bbox` is null in
      // practice today (and the agent-AOI floored bbox may not be persisted on
      // older Cases), so fall back to replaying the LAST `zoom-to` the Case's
      // persisted turns emitted (CaseChatMessage.map_command_emissions in the
      // rehydrated chat_history) through the SAME bus → Map.tsx fitBounds path.
      // extractLastZoomTo walks newest-first, so this is the LATEST (floored)
      // zoom-to — never the first/small pre-floor one. No zoom-to anywhere in
      // history → leave the camera alone (root/new Cases unchanged).
      const replay = extractLastZoomTo(activeSession.chat_history);
      if (replay) {
        bus.pushMapCommand(replay as unknown as MapCommandPayload);
      } else {
        // ux-batch-1 (F14): this Case has no AOI of its own (no bbox, no
        // zoom-to replay). ALWAYS clear any extent left over from the
        // previously viewed Case so switching into a no-AOI Case doesn't
        // inherit a stale rectangle (the Fort-Myers-bbox-shows-in-Chehalis
        // bleed). A Case WITH an AOI replaces the extent via the zoom-to above.
        // (The earlier F28 "skip clear when the Case has layers" was a wrong
        // band-aid: the bleed was actually the dead-basemap stall swallowing
        // the clear command, fixed by the CartoDB basemap swap — so the
        // unconditional clear is correct and bleed-free again.)
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

  // sleep/wake STAGE 2 (NATE 2026-06-18) — COLD-LOAD a Case when the agent box
  // is asleep. "Pen = agent, paper = case": the case must PAINT even with the
  // agent (the pen) asleep. When the user opens a Case while the App socket is
  // NOT connected, the WS `select` only QUEUES (ws.ts sendOrQueue) — it never
  // reaches the agent, so NO `case-open` envelope comes back and the Case never
  // paints. This effect fills that gap: it fetches the agent's persisted S3
  // view-state snapshot via the signer (GET VITE_GRACE2_CASE_VIEW_URL) and feeds
  // the resulting CaseOpenEnvelopePayload through the SAME useCases_onCaseOpen
  // path the live WS uses, so rasters AND inline vectors paint with ZERO new
  // render code.
  //
  // Triggers when: a Case is active (activeCaseId) AND cold-load is configured
  //   AND we haven't already painted a live session for this Case AND the App
  //   socket is NOT connected (connecting / reconnecting / disconnected). The
  //   queued WS `select` stays in flight in parallel; if the box later wakes,
  //   the LIVE case-open re-runs onCaseOpen and supersedes the cold snapshot
  //   (replace_layers semantics + idempotent rail upsert handle the swap).
  //
  // 404 (no snapshot for this Case — never materialised) -> fetchCaseView
  //   returns null -> we leave the Case shell + Wake UI (NOT an error).
  //
  // The ref guards against a re-fetch storm while reconnecting: at most one
  //   cold-load per (caseId) while disconnected; a healthy reconnect or a Case
  //   switch resets it so a later disconnect can cold-load again.
  const coldLoadedCaseRef = useRef<string | null>(null);
  useEffect(() => {
    // Reset the cold-load guard whenever the App socket goes healthy: a live
    // case-open is now authoritative and a future disconnect should be allowed
    // to cold-load again.
    if (wsStatus === "connected") {
      coldLoadedCaseRef.current = null;
      return;
    }
    if (!caseViewConfigured()) return;
    if (activeCaseId === null) return;
    // Already have the live session for this Case (it round-tripped over the WS
    // before the drop) — nothing to cold-load.
    if (activeSession && activeSession.case.case_id === activeCaseId) return;
    // Already cold-loaded this Case during the current disconnected episode.
    if (coldLoadedCaseRef.current === activeCaseId) return;

    coldLoadedCaseRef.current = activeCaseId;
    let cancelled = false;
    // #147 Feature B GAP B2 - forward the signed-in owner's Cognito bearer token
    // (the SAME token ws.ts sends in the `auth-token` handshake) to the signer
    // hop so the view_sign Lambda owner-gates it and mints the 12h OWNER-tier
    // pre-signed URL instead of the anon 15min TTL. Anonymous users (no token)
    // pass `undefined`, so the anon tier is unchanged. getIdToken() never throws
    // here (the .catch collapses any auth-subsystem failure to null -> undefined
    // 3rd arg), so a token hiccup degrades gracefully to the anon path.
    void (async () => {
      const rawToken = await getIdToken().catch(() => null);
      if (cancelled) return;
      const authToken =
        rawToken != null && rawToken.trim() !== "" ? rawToken : undefined;
      const payload = await fetchCaseView(activeCaseId, undefined, authToken);
      if (cancelled || payload === null) {
        // fetchCaseView never throws, but guard belt-and-suspenders: a failed
        // cold-load just leaves the Case shell + Wake UI and releases the guard
        // so a later attempt in the same disconnected episode can re-fetch.
        if (!cancelled) coldLoadedCaseRef.current = null;
        return;
      }
      // Feed the cold snapshot through the SAME path the live WS case-open
      // uses. The rehydration effect above ([activeSession, bus]) then paints
      // it. If a live case-open arrives later it supersedes idempotently.
      useCases_onCaseOpen(payload);
      // job-0179 — ALSO push the case-open onto the bus so Chat (which does
      // not subscribe to App's useCases state) can materialize the COLD
      // chat-history bubbles via routeCaseOpen. Idempotent vs the live WS
      // onCaseOpen below: routeCaseOpen only rebuilds a stream the first time
      // it sees the caseId, so whichever fires first wins and the other is a
      // no-op.
      bus.pushCaseOpen(payload);
    })();
    return () => {
      cancelled = true;
    };
  }, [activeCaseId, wsStatus, activeSession, useCases_onCaseOpen, bus]);

  // sleep/wake STAGE 2 (NATE 2026-06-19) - COLD-LOAD the Cases LIST when the
  // agent box is asleep. SIBLING of the case-VIEW cold-load above: that paints
  // ONE open Case; this paints the Cases ROOT (the left rail) so "paper" renders
  // even with the "pen" (the agent) asleep. When the App socket is NOT connected
  // the WS never delivers a `case-list` frame, so the rail would stay empty until
  // the box wakes. We GET the serverless /case-list snapshot (lib/case_list) and
  // feed it through the SAME useCases_onCaseList path the live WS uses - but with
  // isAuthoritative=true, so a genuinely-empty cold list correctly shows zero
  // cases (the LAST-CASE EDGE FIX in useCases.onCaseList).
  //
  // Fires ONCE per (signed-in identity) ref guard only while: the App socket is
  // NOT connected AND cold-load is configured AND the rail is still empty
  // (cases.length === 0). A later live `case-list` over the WS supersedes it
  // (non-empty replaces; the reconcile is idempotent). Gated to dev/LAN safety
  // by caseListConfigured() (null endpoint -> no fetch).
  //
  // COLD-LIST SIGNED-IN FIX (NATE 2026-06-19) — NATE is signed in (Cognito) but
  // saw an EMPTY rail with the box asleep. Root cause: the effect fired on mount
  // BEFORE auth resolved, so `getIdToken()` returned null; the Lambda's auth
  // contract answers a tokenless request with an AUTHORITATIVE 200 EMPTY list
  // (never 401), which is a non-null payload — so the old guard LATCHED `true`
  // and NEVER re-fetched once the token arrived (its deps `[wsStatus,
  // cases.length, ...]` don't change on sign-in). Two changes fix it:
  //   1. Depend on the signed-in identity (`coldListIdentity` = the uid, or
  //      "anon"); the guard is keyed to it (`coldLoadedListIdRef`) so when auth
  //      resolves / the user signs in, the identity flips and the effect RE-RUNS
  //      with the now-available token.
  //   2. NEVER cold-load WITHOUT the token when the user IS signed in: if
  //      `isSignedIn` but `getIdToken()` came back null (token not ready yet),
  //      skip the fetch and release the guard so the next identity-keyed run (or
  //      a token-ready re-render) retries — we must not burn the one attempt on
  //      a tokenless request that the Lambda would answer empty.
  const coldLoadedListIdRef = useRef<string | null>(null);
  const coldListIdentity = isSignedIn ? authUser?.uid ?? "signed-in" : "anon";
  useEffect(() => {
    // Reset the cold-load-list guard whenever the App socket goes healthy:
    // a live `case-list` is now authoritative and a future disconnect should
    // be allowed to cold-load the rail again. Mirrors coldLoadedCaseRef's
    // reset-on-reconnect above; without this the ref latched forever after the
    // first cold session, so a later cold session never re-fetched.
    if (wsStatus === "connected") {
      coldLoadedListIdRef.current = null;
      return;
    }
    // Already cold-loaded for THIS identity during the current disconnected
    // episode. A sign-in (identity flip) clears this by inequality below.
    if (coldLoadedListIdRef.current === coldListIdentity) return;
    if (!caseListConfigured()) return;
    if (cases.length > 0) return;

    coldLoadedListIdRef.current = coldListIdentity;
    let cancelled = false;
    void (async () => {
      const token = await getIdToken().catch(() => null);
      if (cancelled) return;
      // Signed in but the token is not ready yet — do NOT spend the attempt on a
      // tokenless request (the Lambda would answer an authoritative empty list).
      // Release the guard so an identity-keyed / token-ready re-run retries.
      if (isSignedIn && (token == null || token.trim() === "")) {
        coldLoadedListIdRef.current = null;
        return;
      }
      const payload = await fetchCaseList(undefined, token);
      // A failed / null cold-load releases the guard so a later attempt in the
      // same disconnected episode can re-fetch (mirrors coldLoadedCaseRef on
      // fetch failure). A successful non-null payload keeps the guard set for
      // this identity.
      if (cancelled || payload === null) {
        if (!cancelled) coldLoadedListIdRef.current = null;
        return;
      }
      // Cold FETCH is AUTHORITATIVE: an empty list genuinely means zero cases
      // (clears the rail); a non-empty list paints it.
      useCases_onCaseList(payload, true);
    })();
    return () => {
      cancelled = true;
    };
  }, [wsStatus, cases.length, coldListIdentity, isSignedIn, useCases_onCaseList]);

  // Lift layers from session-state.
  //
  // job-0179 (per-Case client cache — "the seatbelt"): route the incoming layer
  // SET through cache.mergeSnapshot so a STALE / partial / reconnect frame that
  // OMITS a layer ADDS/REFRESHES but never EVICTS it; the rendered list comes
  // from cache.layersFor(active Case). `replace_layers` is App's authoritative
  // stamp (true on a Case switch/exit or a healthy non-empty server frame;
  // false on a transient reconnect frame) — it gates whether omitted layers may
  // be dropped. At the root (no active Case) there is no Case to cache against,
  // so mergeSnapshot passes the list through verbatim (byte-identical to before).
  useEffect(() => {
    const unsub = bus.subscribeSessionState((p) => {
      const incoming = p.loaded_layers ?? [];
      const authoritativeReplace =
        (p as { replace_layers?: boolean }).replace_layers !== false;
      const caseId = layerCache.activeCaseId;
      const merged = layerCache.mergeSnapshot(caseId, incoming, {
        authoritativeReplace,
      });
      setLayers(merged);
    });
    return unsub;
  }, [bus, layerCache]);

  // Dev-only debug seam.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectSessionState = (p) => bus.pushSessionState(p);
    window.__grace2InjectMapCommand = (p) => bus.pushMapCommand(p);
    window.__grace2InjectSecretsList = (p) => setSecrets(p.secrets ?? []);
    window.__grace2InjectSourceSuggestion = (p) => fanoutSourceSuggestion(p);
    window.__grace2InjectCaseList = (p) => useCases_onCaseList(p);
    window.__grace2InjectCaseOpen = (p) => useCases_onCaseOpen(p);
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
      delete window.__grace2InjectImpactEnvelope;
      delete window.__grace2InjectChartEmission;
      delete window.__grace2ClearCharts;
    };
  }, [bus, fanoutSourceSuggestion, useCases_onCaseList, useCases_onCaseOpen, handleChartEmission]);

  // job-0179 — per-layer delete is an EXPLICIT eviction: drop the layer (and its
  // persisted view-override) from the shared cache so the seatbelt's allowsEvict
  // permits Map.tsx to tear the overlay down, THEN tell the server (which echoes
  // a fresh session-state sans the layer). Without the cache delete, allowsEvict
  // would protect the just-deleted layer against the authoritative replace.
  const handleDeleteLayer = useCallback(
    (id: string): void => {
      layerCache.deleteLayer(layerCache.activeCaseId, id);
      wsRef.current?.sendDeleteLayer(id);
    },
    [layerCache],
  );

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

  // FIX 2 — payload-warning gates render in Chat's per-Case stream now (no
  // App-level filtering / banner). See Chat.tsx routePayloadWarning.

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
        /* Lift the projected AOI rect so the SequenceScrubber (inside
           LayerPanel) can pin bottom-center of the AOI box like the legend. */
        onAoiScreenRectChange={setAoiScreenRect}
      />

      {/* MOBILE TOP FROST GRADIENT (NATE 2026-06-19) — with the iOS status bar
          now translucent (apple-mobile-web-app-status-bar-style=black-translucent
          in index.html), the page runs UNDER the time/battery, so more map shows
          but those glyphs can wash out over a light basemap. This thin
          top-anchored dark->transparent gradient sits behind the status-bar area
          (height = the safe-area inset + a small amount) to keep them legible.
          pointer-events:none so the map underneath stays fully draggable; mobile
          only (desktop has no status bar to cover). z-index above the map but
          below the rail/hamburgers/overlays. */}
      {isMobile && (
        <div
          data-testid="grace2-mobile-top-frost"
          aria-hidden
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "calc(env(safe-area-inset-top) + 14px)",
            pointerEvents: "none",
            zIndex: 15,
            background:
              "linear-gradient(180deg, rgba(10,11,15,0.55) 0%, rgba(10,11,15,0) 100%)",
          }}
        />
      )}

      {/* job-0321 F43 — the layer legend/colorbar is no longer an App-level
          floating element. It now renders INSIDE Map.tsx, anchored to the
          bottom edge of the AOI bounding box (Group A owns that placement) so
          it reads as the key for that AOI. The App-level <LayerLegend> render
          (and its mobile-offset wrapper) is removed here. */}

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
            left: 16,
            // top + bottom => a real bounded pixel height (100vh - 12 - 12)
            // for the child CasesPanel (height:100%) to fill. The old
            // maxHeight: calc(100vh - 80px) only capped a content-sized
            // (height:auto) wrapper, so the panel never got squeezed below
            // content and its inner list never scrolled.
            bottom: 12,
            zIndex: 20,
            // flex column so CasesPanel's height:100% resolves against this
            // bounded-height wrapper; minHeight:0 lets the column squeeze.
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
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
              left: 16,
              zIndex: 22,
              // Match CaseView's own 288px wrapStyle exactly. The prior 280px
              // here was 8px NARROWER than the CaseView it contained, so the
              // breadcrumb sized its title against 288 while the visible rail
              // was 280 — the long-title right edge fell outside the wrapper
              // and hard-clipped mid-glyph (the recurring cutoff). Aligning the
              // widths lets CaseView's own ellipsis budget match the rail.
              width: 288,
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
                  width: 288,
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
                  /* Projected AOI rect (lifted from MapView) so the
                     SequenceScrubber pins bottom-center of the AOI box. */
                  aoiRect={aoiScreenRect}
                  /* job-0258: user layer-control intents (opacity slider /
                     visibility checkbox / drag-reorder) flow through the bus
                     so MapView applies them to the live MapLibre instance.
                     Without this the panel controls were dead in the demo. */
                  onMapCommand={bus.pushMapCommand}
                  /* job-0322 F53 — end-to-end delete. The LayerPanel delete
                     control (job-0325) optimistically removes the row, but the
                     layer resurrected on the next session-state because this
                     prop was never wired: the client never told the server.
                     sendDeleteLayer emits the `layer-delete` envelope; the
                     server persists the post-deletion list and echoes a fresh
                     session-state (sans the layer) which onSessionState →
                     bus.pushSessionState reconciles into the Map via
                     replace-not-reconcile — so the layer stays gone. */
                  onDeleteLayer={handleDeleteLayer}
                />
              </div>
            </div>
          )}
        </>
      )}

      {/* job-0143: Bottom-row Settings pill. Hidden when the left rail is
          collapsed (it belongs to the rail). job-0278: on mobile it folds
          into the drawer footer instead — the floating pill would collide
          with the bottom-sheet composer.
          job-0321 F29 — the standalone Secrets pill is retired (API keys now
          live inside Settings), so `onOpenSecrets` is no longer wired. */}
      {!isMobile && !leftCollapsed && (
        <BottomRowButtons
          onOpenSettings={() => setSettingsOpen(true)}
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
          /* sleep/wake STAGE 2 — App owns the asleep classification (App socket
             + report-only probe) and threads it down so Chat's composer machine
             can branch Connecting -> (Chat | Wake). `agentAsleep` =
             composerWakeReady; `onWakeTap` is the ONLY POST-wake path (tap
             only). Chat gates ONLY the composer; its scrollback stays live. */
          agentAsleep={composerWakeReady}
          onWakeTap={handleWakeTap}
          /* job-0179 — COLD chat-history render. App routes every case-open
             (live WS + cold serverless snapshot) onto the bus; Chat subscribes
             here to materialize the per-Case chat-history bubbles via
             routeCaseOpen. Chat does NOT see App's useCases state, so without
             this the cold view leaves the conversation blank. Idempotent. */
          subscribeCaseOpen={bus.subscribeCaseOpen}
        />
      </div>

      {/* sleep/wake STAGE 2 (NATE 2026-06-18) — the OLD full-chat-panel
          WakeOverlay mount is REMOVED. It blanketed the entire chat panel
          (scrollback included) and was driven by the App socket. STAGE 2 keeps
          the chat scrollback + tool cards + insights AND the whole map LIVE with
          the box asleep, gating ONLY the text-entry composer. The Wake UI now
          lives INSIDE Chat's composer slot (Chat.tsx renders WakeOverlay scoped
          to that slot), driven by the asleep signal (`composerWakeReady`) +
          tap handler (`handleWakeTap`) threaded down via the Chat props below. */}

      {/* Layers hamburger — top-LEFT. (Desktop; mobile uses the drawer ☰.) */}
      {!isMobile && showLayersHamburger && (
        <button
          data-testid="grace2-layers-hamburger"
          aria-label="Show layers"
          aria-expanded={false}
          aria-controls="grace2-layer-panel"
          onClick={expandLeft}
          style={{ ...hamburgerBtnStyle, left: 16 }}
        >
          {/* job-0322 F52 — icon-module glyph (no raw unicode ☰). */}
          <IconMenu size={18} />
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
          {/* job-0322 F52 — icon-module glyph (no raw unicode ☰). */}
          <IconMenu size={18} />
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

      {/* job-0321 F29 — mobile-only top-right Settings entry. On mobile the
          only prior Settings reach was buried in the drawer footer; this puts
          a ⚙ button at the top-right so Settings (and, bundled inside it, the
          API-key entry) is reachable from anywhere. Desktop is unaffected
          (Settings stays on the bottom-row pill).
          z-index 36 sits above the upgrade toast (35) but below the
          payload-warning banner "hat" (60) and the Settings overlay itself
          (9500), and clears the toast which anchors at top:56. */}
      {isMobile && (
        <button
          data-testid="grace2-mobile-settings-button"
          aria-label="Open settings"
          onClick={() => setSettingsOpen(true)}
          style={{
            position: "absolute",
            top: 12,
            right: 12,
            width: 44,
            height: 44,
            padding: 0,
            background: "rgba(18,19,24,0.85)",
            border: "1px solid rgba(255,255,255,0.10)",
            borderRadius: 12,
            boxShadow: "0 2px 12px rgba(0,0,0,0.25)",
            color: "#cfd4db",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            lineHeight: 1,
            zIndex: 36,
            fontFamily:
              "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
          }}
        >
          {/* job-0322 F29 — icon-module gear (no raw unicode ⚙). */}
          <IconSettings size={20} />
        </button>
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
            // job-0322 F52 (v2) — the layout wrapper is click-transparent so
            // empty/gutter taps fall through to the drawer backdrop (close);
            // the inner wrapper hugs the CasesPanel card and re-enables
            // hit-testing (`pointerEvents: "auto"`) so the card (and the
            // fixed ConfirmationDialog it mounts) still receive taps.
            <div
              style={{
                flex: 1,
                minHeight: 0,
                // CasesPanel scrolls its OWN list internally (pinned header +
                // mask fade); the hugger must NOT double-scroll. flex:1 +
                // minHeight:0 already give it a bounded height from the
                // MobileDrawer column (top:0/bottom:0); overflow:hidden (was
                // overflowY:auto) removes the competing scroll container so
                // CasesPanel height:100% fills this bound and the list — not
                // the whole panel including the header — is what scrolls.
                overflow: "hidden",
                pointerEvents: "none",
              }}
            >
              {/* job-0337 — the hugger stays full-width so it never shrink-
                  wraps to a long Case title's intrinsic width (the job-0330
                  clip hazard). The CasesPanel inside is now a FIXED 288px
                  (max-width:100% guards sub-288 columns) — it neither grows
                  with content nor varies with viewport — so the row title's
                  flex:1 + min-width:0 ellipsis engages and the kebab
                  (flex-shrink:0) stays inside the column's overflow:hidden
                  clip. (The mobile fixed width is set in global.css
                  `.grace2-mobile-touch [data-testid="grace2-cases-panel"]`.) */}
              <div style={{ width: "100%", pointerEvents: "auto" }}>
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
            </div>
          ) : (
            <>
              {/* job-0284 — mobile: the "Cases" breadcrumb link is the
                  SINGLE back affordance (no ← arrow).
                  job-0322 F52 (v2) — wrap in a `pointerEvents: "auto"` hugger
                  so the breadcrumb card stays tappable even though the drawer
                  column above is click-transparent (gutter taps fall through to
                  the backdrop = close). */}
              {/* NATE 2026-06-19: fill the drawer width (was width:"fit-content",
                  which sized to CaseView's old fixed 288px wrap and overflowed
                  narrow phones -> breadcrumb cutoff). 100% + min-width:0 lets the
                  breadcrumb bound to the real drawer width and ellipsize. */}
              <div style={{ width: "100%", minWidth: 0, pointerEvents: "auto" }}>
                <CaseView
                  caseTitle={activeCase?.title ?? "Case"}
                  onBack={handleCaseBack}
                  mobile
                />
              </div>
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
                    // job-0322 F52 (v2) — this card is an actual component, so
                    // it re-enables hit-testing above the click-transparent
                    // drawer column. (It has no interactive controls today, but
                    // keeping it `auto` matches the spec and is forward-safe.)
                    pointerEvents: "auto",
                  }}
                >
                  No layers loaded yet. Ask the assistant to add data.
                </div>
              ) : (
                <div
                  style={{
                    position: "relative",
                    flex: 1,
                    minHeight: 0,
                    // job-0322 F52 (v2) — the LayerPanel layout wrapper is
                    // click-transparent so gutter taps around the panel fall
                    // through to the backdrop (close). LayerPanel itself
                    // re-enables hit-testing via the `auto` wrapper below.
                    pointerEvents: "none",
                  }}
                >
                  {/* LayerPanel positions itself absolutely (left:16 /
                      top:16 / bottom:16 / width:288) relative to this
                      wrapper — it fills the drawer column.
                      job-0322 F52 (v2) — `pointerEvents: "auto"` wrapper
                      restores hit-testing for the absolutely-positioned panel
                      (pointer-events inherits down the DOM tree regardless of
                      layout position). */}
                  <div style={{ pointerEvents: "auto" }}>
                  <LayerPanel
                    subscribeSessionState={bus.subscribeSessionState}
                    subscribeMapCommand={bus.subscribeMapCommand}
                    initialLayers={layers}
                    onClose={() => setMobileDrawerOpen(false)}
                    onMapCommand={bus.pushMapCommand}
                    /* Projected AOI rect (lifted from MapView) so the mobile
                       SequenceScrubber pins bottom-center of the AOI box. */
                    aoiRect={aoiScreenRect}
                    /* job-0322 F53 — end-to-end delete on the mobile drawer
                       mount too (swipe-right-to-delete in Group C drives this
                       same callback). See the desktop mount above for the
                       full data-flow rationale. */
                    onDeleteLayer={handleDeleteLayer}
                    mobile
                  />
                  </div>
                </div>
              )}
            </>
          )}
          {/* job-0322 F29 — the drawer-footer Settings pill is REMOVED. The
              mobile-only top-right gear button (grace2-mobile-settings-button,
              above) is now the SOLE mobile Settings entry; API keys still live
              inside the SettingsPopup it opens. The desktop bottom-left
              BottomRowButtons pill is unchanged. */}
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
          {/* FIX 2 (NATE 2026-06-17): payload-warning gates are no longer here
              NOR in any App-level banner — they render as in-chat cards in
              Chat's per-Case stream (Chat.tsx). Source suggestions stay here. */}
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

      {/* FIX 2 (NATE 2026-06-17) — the large-payload warning BANNER "hat" is
          GONE. The warning is now an IN-CHAT card interleaved in the per-Case
          chat scroll (Chat.tsx kind:"payload-warning", PayloadWarningInline),
          matching the credential / tool / sandbox card family. tool-payload-
          warning is session-scoped (ws.ts SESSION_SCOPED_TYPES) so Chat's own
          GraceWs receives it via the fan-out hub; App no longer renders or
          tracks it. */}

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
          /* job-0321 F29 — bundle the per-Case API-key entry INSIDE Settings.
             These are the SAME wires that previously fed the standalone
             SecretsPopup; SettingsPopup renders SecretsPanel inline under its
             "API Keys" section. */
          secrets={secrets}
          caseId={currentCaseId}
          onSecretAdd={handleSecretAdd}
          onSecretRevoke={handleSecretRevoke}
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

      {/* job-0321 F29 — the standalone Secrets popup is retired. API-key
          management now lives inside the Settings popup above (SettingsPopup's
          embedded SecretsPanel), wired with the same secrets/case/add/revoke
          props. */}

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

      {/* JOB WEB-ANIM (#157.2-.3) — the floating sequence SCRUBBER. Rendered at
          the App root (always mounted) so it shows WHENEVER a sequential group is
          active on the shared AnimationController, regardless of whether the
          Layers panel is open/collapsed. It pins bottom-center of the AOI box via
          aoiScreenRect. Carries its own play/pause button (item 3) wired to the
          controller, so closing the panel never drops the scrubber or playback. */}
      <AppSequenceScrubber aoiRect={aoiScreenRect} />
    </div>
    </AuthGuard>
  );
}

// --- App-level sequence scrubber (JOB WEB-ANIM #157.2-.3) --------------- //
//
// The scrubber used to render from inside LayerPanel, so closing the panel
// dropped it (and, since the play interval also lived there, killed playback).
// It now lives here, driven entirely by the module-level AnimationController, so
// it appears whenever ANY sequence is active — panel open or not. Stepping +
// play/pause go straight to the controller (which drives the map + the interval);
// the LayerPanel, when open, mirrors the controller's frame into its own rows.
function AppSequenceScrubber({
  aoiRect,
}: {
  aoiRect: ScreenRect | null;
}): JSX.Element | null {
  const controller = useMemo(() => getAnimationController(), []);
  const anim = useAnimationState(controller);
  const activeGroup =
    anim.activeGroupKey != null
      ? anim.groups.find((g) => g.key === anim.activeGroupKey) ?? null
      : null;
  if (!activeGroup) return null;
  const activeIndex = controller.frameIndexFor(activeGroup.key);
  return (
    <SequenceScrubber
      label={activeGroup.label}
      frameLabels={activeGroup.frameLabels}
      activeIndex={activeIndex}
      onStep={(idx) => controller.stepGroupTo(activeGroup.key, idx)}
      playing={anim.playing}
      onPlayToggle={() => {
        controller.setActiveGroup(activeGroup.key);
        controller.togglePlaying();
      }}
      aoiRect={aoiRect}
    />
  );
}
