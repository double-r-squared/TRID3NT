// GRACE-2 web — top-level shell.
//
// job-0068 layout (overlay panels over full-viewport map):
//
//   +-----------------------------------------------------------+
//   |  [☰ Layers] (TL hamburger, when left hidden)              |
//   |                                            [☰ Chat] (TR)  |
//   |                                                           |
//   |   [LayerPanel] (left overlay, 280px)   [Chat] (right)     |
//   |                                                           |
//   |                       Map (full bleed)                    |
//   |                                       [LayerLegend] (BC)  |
//   +-----------------------------------------------------------+
//
// Reverts sprint-9's flex-row split-pane to the original M3 intent:
//   - Map is full-viewport (position: fixed, inset: 0)
//   - Panels are position:absolute overlays floating ABOVE the map
//   - Collapsed = panel fully hidden + hamburger icon on same side (TL/TR)
//   - Left panel only mounts when layers.length > 0 (no empty-tab bug)
//
// job-0064 (Option A): PipelineStrip deleted. Pipeline cards now render
// inline in the Chat stream (FR-WC-8). The basemap stays clean. The
// cancel button lives in Chat's footer (FR-WC-9; Invariant 8).
//
// Subscription wiring: bus is shared between LayerPanel, MapView, and the
// App's own session-state subscriber (which lifts `layers` for LayerLegend
// and the conditional-mount gate). Chat.tsx owns its own GraceWs.
//
// Dev-only debug seam: in dev mode the App attaches
// `window.__grace2InjectSessionState`, `window.__grace2InjectMapCommand`,
// and `window.__grace2InjectPipelineState` (pipeline injection is wired by
// Chat.tsx via its own GraceWs handler) so a local browser console can
// seed components without an agent.

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
import { SecretsPanel } from "./components/SecretsPanel";
import { CasesPanel } from "./components/CasesPanel";
import { PersistenceChip } from "./components/PersistenceChip";
import {
  Mode2OfferAction,
  Mode2OfferModal,
} from "./components/Mode2OfferModal";
import { PayloadWarningInline } from "./components/PayloadWarningInline";
import { AuthUser, onAuthChanged, signOut as authSignOut } from "./auth";
import { GraceWs } from "./ws";
import { Mode2CandidatePayload } from "./lib/mode2_suppression";
import { useCases } from "./hooks/useCases";
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
// Visibility of the SecretsPanel overlay (job-0125). Persisted so a reload
// keeps the panel open when the user was actively managing keys.
const LS_SECRETS_OPEN = "grace2.secretsPanelOpen";

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
    /** Dev seam for secrets-list (job-0125); wired by App.tsx GraceWs handler. */
    __grace2InjectSecretsList?: (p: SecretsListPayload) => void;
    /** Dev seam for mode2-candidate (job-0126); wired by App.tsx GraceWs handler. */
    __grace2InjectMode2Candidate?: (p: Mode2CandidatePayload) => void;
    /** Dev seam for case-list (job-0137); wired by App.tsx GraceWs handler. */
    __grace2InjectCaseList?: (p: CaseListEnvelopePayload) => void;
    /** Dev seam for case-open (job-0137); wired by App.tsx GraceWs handler. */
    __grace2InjectCaseOpen?: (p: CaseOpenEnvelopePayload) => void;
    /** Dev seam for payload-warning (job-0140); wired by App.tsx GraceWs handler. */
    __grace2InjectPayloadWarning?: (p: PayloadWarningEnvelopePayload) => void;
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
  // the user's preference. leftCollapsed only matters when layers.length > 0.
  const [leftCollapsed, setLeftCollapsed] = useState<boolean>(() =>
    readCollapsed(LS_LEFT_COLLAPSED),
  );
  const [rightCollapsed, setRightCollapsed] = useState<boolean>(() =>
    readCollapsed(LS_RIGHT_COLLAPSED),
  );

  // Layers lifted here from session-state so:
  //   (a) LayerLegend can read the list
  //   (b) we can gate the left panel conditional mount on layers.length > 0
  // Sourced directly from bus subscription (not via onLayersChange callback
  // from LayerPanel) so it works even when LayerPanel isn't mounted.
  const [layers, setLayers] = useState<ProjectLayerSummary[]>([]);

  // Map theme (job-0076): light = QGIS Server WMS basemap (default);
  // dark = CartoDB DarkMatter raster. Persisted in localStorage so reloads
  // remember user preference.
  const [theme, setTheme] = useState<MapTheme>(() => readTheme());

  // Auth state (job-0123, sprint-12-mega Wave 2). Subscribed at the App level
  // so the AuthGate / residual indicator and any future auth-gated UI
  // (Cases, share links) can be driven off this single source. When Firebase
  // is unconfigured (anonymous-only dev) the subscription resolves to null
  // once and stays.
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  // Tracks whether the auth subscription has emitted at least once. Before
  // the first emission we don't know if the user is signed in (Firebase
  // restoring a cached session is async), so we hold the AuthGate render
  // back until we hear from it — otherwise a signed-in user briefly sees
  // the gate on every load.
  const [authResolved, setAuthResolved] = useState<boolean>(false);
  useEffect(() => {
    const unsub = onAuthChanged((u) => {
      setAuthUser(u);
      setAuthResolved(true);
    });
    return unsub;
  }, []);

  // job-0138: anonymous-accepted flag. Replaces the old AuthPanel's
  // "Continue as anonymous" button — the user now lands on a full-page
  // AuthGate and must explicitly choose to proceed without saving. The flag
  // is persisted in localStorage so reloads bypass the gate, and is cleared
  // on sign-out or anonymous→authenticated upgrade so subsequent visits
  // honour the new state.
  const [anonymousAccepted, setAnonymousAccepted] = useState<boolean>(() =>
    readAnonymousAccepted(),
  );
  // Upgrade-to-authenticated toast (kickoff item 6): cleared after a few
  // seconds. Null means no toast showing.
  const [upgradeToast, setUpgradeToast] = useState<string | null>(null);
  // Track the previous-render auth identity so we can detect the
  // "was anonymous-flag + now authenticated" transition exactly once.
  const prevSignedInRef = useRef<boolean>(false);
  useEffect(() => {
    const nowSignedIn = !!authUser && !authUser.isAnonymous;
    const wasSignedIn = prevSignedInRef.current;
    prevSignedInRef.current = nowSignedIn;
    if (nowSignedIn && !wasSignedIn && anonymousAccepted) {
      // Anonymous → authenticated upgrade. Clear the flag so future visits
      // don't bypass the gate with a stale anonymous accept, and surface a
      // welcome toast (kickoff item 6).
      clearAnonymousAccepted();
      setAnonymousAccepted(false);
      setUpgradeToast("Welcome back — your Cases will now sync");
      const t = setTimeout(() => setUpgradeToast(null), 4500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [authUser, anonymousAccepted]);

  // Gate render rule (kickoff §2): show the app only when we either have an
  // authenticated (non-anonymous) Firebase user OR the user has explicitly
  // accepted the anonymous flow. A Firebase anonymous-sign-in user is NOT
  // sufficient to bypass — the flag is the explicit consent signal.
  const appShouldRender: boolean =
    authResolved &&
    ((!!authUser && !authUser.isAnonymous) || anonymousAccepted);

  // AuthGate handlers — wired to the real auth.ts surface in production;
  // overridable for tests.
  const handleAnonymousAccept = useCallback(() => {
    setAnonymousAccepted(true);
  }, []);
  const handleSignOut = useCallback(async () => {
    try {
      await authSignOut();
    } catch {
      // Sign-out failures are non-fatal — we still clear local state so the
      // user returns to the gate.
    }
    clearAnonymousAccepted();
    setAnonymousAccepted(false);
    // authResolved stays true so we render the gate (not a flash of nothing)
    // — onAuthChanged will fire with null and update authUser.
  }, []);

  // Secrets state (job-0125, sprint-12-mega Wave 2 — SRS §F.3 per-Case API
  // keys). The list is driven by `secrets-list` envelopes the agent emits;
  // the SecretsPanel reads it. The ref to the active GraceWs lets the panel
  // emit `secret-add` / `secret-revoke` envelopes via the same socket the
  // App opens for layer routing.
  const [secrets, setSecrets] = useState<SecretRecord[]>([]);
  const [secretsPanelOpen, setSecretsPanelOpen] = useState<boolean>(() => {
    try { return localStorage.getItem(LS_SECRETS_OPEN) === "true"; }
    catch { return false; }
  });
  const wsRef = useRef<GraceWs | null>(null);

  // job-0137 (sprint-12-mega Wave 3): Cases UX shell. The useCases hook wraps
  // the case-list / case-open envelopes + the case-command emitters. We pass
  // a stable `sendCaseCommand` that proxies through wsRef so the hook does
  // not need a WS reference of its own. Anonymous users still get cases (the
  // agent's session_id placeholder path) but the PersistenceChip surfaces
  // "Sign in to save" so the user knows they should authenticate before
  // relying on long-term persistence.
  const sendCaseCommand = useCallback(
    (command: Parameters<GraceWs["sendCaseCommand"]>[0], caseId: string | null, args: Record<string, unknown>) => {
      wsRef.current?.sendCaseCommand(command, caseId, args);
    },
    [],
  );
  const isSignedIn = !!authUser && !authUser.isAnonymous;
  const {
    cases,
    activeCaseId,
    activeSession,
    persistenceState,
    onCaseList: useCases_onCaseList,
    onCaseOpen: useCases_onCaseOpen,
    createCase,
    selectCase,
    renameCase,
    archiveCase,
    deleteCase,
    // clearActive is exposed by useCases for future "Close Case" affordance;
    // not currently surfaced in the UI but referenced via the hook return.
  } = useCases({ sendCaseCommand, isSignedIn });

  // job-0125 wiring update: the currentCaseId now actually has a source.
  // Secrets emitted from SecretsPanel are scoped to the active Case when one
  // is selected; user-wide when null.
  const currentCaseId: string | null = activeCaseId;

  // job-0127 (sprint-12-mega Wave 2): tool payload-warning gates. The agent
  // emits `tool-payload-warning` before dispatching a tool whose estimated
  // response payload exceeds the threshold; we render an inline card and
  // emit `tool-payload-confirmation` carrying the user's decision.
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
      // Remove the decided warning from the visible queue. Keep the others
      // so a multi-gate burst still renders.
      setPayloadWarnings((prev) =>
        prev.filter((w) => w.warning_id !== warningId),
      );
    },
    [],
  );

  // job-0126: Mode 2 candidate fan-out. The GraceWs handler routes
  // `mode2-candidate` envelopes here; the Mode2OfferModal subscribes via the
  // stable `subscribeMode2Candidate` callback below. We keep a Set of
  // subscribers so a future settings UI could observe candidates without
  // displacing the modal. In practice there's one subscriber (the modal).
  const mode2SubscribersRef = useRef<
    Set<(p: Mode2CandidatePayload) => void>
  >(new Set());
  const subscribeMode2Candidate = useCallback(
    (cb: (p: Mode2CandidatePayload) => void) => {
      mode2SubscribersRef.current.add(cb);
      return () => {
        mode2SubscribersRef.current.delete(cb);
      };
    },
    [],
  );
  const fanoutMode2Candidate = useCallback(
    (p: Mode2CandidatePayload) => {
      mode2SubscribersRef.current.forEach((cb) => {
        try {
          cb(p);
        } catch {
          // Subscriber threw — log but never let one bad subscriber take
          // down sibling subscribers or the WS handler chain.
          // eslint-disable-next-line no-console
          console.error("[mode2] subscriber threw");
        }
      });
    },
    [],
  );

  // Bridge the Mode2OfferModal action callback to the active GraceWs.
  // Emits the `mode2-add-confirmed` envelope on "add" + a `mode2-audit-event`
  // on every action ("add" / "dismiss" / "suppress") so the server-side log
  // captures the full lifecycle.
  const handleMode2Action = useCallback((action: Mode2OfferAction) => {
    const ws = wsRef.current;
    const c = action.candidate;
    // Confidence-derived surface, so the server can attribute the event to
    // either the modal or the toast path.
    const surface: "modal" | "toast" = c.confidence >= 0.7 ? "modal" : "toast";
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
      surface,
    });
    // Mirror to console.debug so a developer running without an agent can
    // still see the audit lifecycle (kickoff §3 "audit log entry").
    // eslint-disable-next-line no-console
    console.debug(
      `[mode2-audit] ${action.kind} surface=${surface} domain=${c.domain} candidate=${c.candidate_id}`,
    );
  }, []);

  function openSecretsPanel(): void {
    setSecretsPanelOpen(true);
    try { localStorage.setItem(LS_SECRETS_OPEN, "true"); } catch { /* non-fatal */ }
  }

  function closeSecretsPanel(): void {
    setSecretsPanelOpen(false);
    try { localStorage.setItem(LS_SECRETS_OPEN, "false"); } catch { /* non-fatal */ }
  }

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

  // Mount a GraceWs that routes session-state, map-command, AND secrets-list
  // envelopes. Chat.tsx handles pipeline-state and agent messages via its own
  // GraceWs.
  useEffect(() => {
    const ws = new GraceWs(WS_URL, {
      onStatus: () => {
        // status indicator is on the Chat panel; not duplicated here.
      },
      onAgentChunk: () => {
        // Chat panel handles agent message rendering.
      },
      onPipelineState: () => {
        // Chat.tsx handles pipeline-state in its own GraceWs.
      },
      onSessionState: (p) => {
        bus.pushSessionState(p);
      },
      onMapCommand: (p) => bus.pushMapCommand(p),
      onSecretsList: (p) => {
        // job-0125: surface §F.3 secrets-list to the SecretsPanel.
        setSecrets(p.secrets ?? []);
      },
      onMode2Candidate: (p) => {
        // job-0126: fan out the Mode 2 candidate to the offer modal.
        fanoutMode2Candidate(p);
      },
      onPayloadWarning: (p) => {
        // job-0127: surface the payload-warning card. We keep a queue so a
        // burst of warnings (multi-tool workflow) renders newest-first
        // without dropping older gates the user hasn't decided yet.
        setPayloadWarnings((prev) => [p, ...prev]);
      },
      onCaseList: (p: CaseListEnvelopePayload) => {
        // job-0137: refresh the left-rail list. The useCases hook owns the
        // state; we just forward the envelope.
        useCases_onCaseList(p);
      },
      onCaseOpen: (p: CaseOpenEnvelopePayload) => {
        // job-0137: rehydrate the Case session. The useCases hook drives the
        // chat history, loaded layers, and map view replay (App.tsx
        // synthesizes a session-state envelope below to feed the existing
        // bus subscribers, then optionally fitBounds to case.bbox via a
        // load-style map-command).
        useCases_onCaseOpen(p);
      },
      onError: () => {
        // Chat panel renders connection errors.
      },
    });
    wsRef.current = ws;
    ws.connect();
    return () => {
      wsRef.current = null;
      ws.close();
    };
  }, [bus]);

  // job-0137: Case rehydration replay. When a `case-open` envelope arrives,
  // useCases stores `activeSession`. We project that session into the
  // existing bus so the LayerPanel / Map / LayerLegend re-bind WITHOUT any
  // new wiring — the SessionStatePayload shape carries `loaded_layers` and
  // `map_view`, which is exactly what `session-state` envelopes carry. We
  // also emit a `zoom-to` map-command if the Case has a bbox so the map
  // re-centers on the Case AOI.
  //
  // When activeSession transitions to null (archived/deleted/clearActive),
  // we push an empty session-state so the left panel hides and the map
  // resets back to CONUS default (the bus is the single source of truth for
  // layer state).
  useEffect(() => {
    if (activeSession === null) {
      // Clear layers cleanly back to empty so LayerPanel + LayerLegend
      // reflect "no Case open".
      bus.pushSessionState({
        loaded_layers: [],
        chat_history: [],
        pipeline_history: [],
        current_pipeline: null,
        map_view: null,
      });
      return;
    }
    // Project CaseSessionState onto the SessionStatePayload shape the bus
    // expects. The fields are intentionally compatible (D.6 surface).
    bus.pushSessionState({
      loaded_layers: activeSession.loaded_layers ?? [],
      chat_history: activeSession.chat_history ?? [],
      pipeline_history: activeSession.pipeline_history ?? [],
      current_pipeline: activeSession.current_pipeline ?? null,
      map_view: null, // map_view is per-Case; we use bbox below instead
    });
    // If the Case carries a bbox, dispatch a zoom-to map-command so the
    // map fits the AOI. We use the same bus the live `map-command` envelope
    // path uses — the existing Map.tsx handler (job-0068) processes zoom-to
    // via fitBounds.
    const bbox = activeSession.case.bbox;
    if (bbox && bbox.length === 4) {
      // Map.tsx (job-0068) zoom-to handler reads `args.bbox` — the M3-deferred
      // command shape per WireMapCommand. Cast through unknown is intentional
      // since MapCommandPayload doesn't include zoom-to in v0.1 (FR-WC-12).
      bus.pushMapCommand({
        command: "zoom-to",
        args: { bbox },
      } as unknown as MapCommandPayload);
    }
  }, [activeSession, bus]);

  // Lift layers from session-state so we can gate the left panel mount.
  // This subscription is separate from LayerPanel's own subscription so
  // layers are tracked even when the panel is unmounted (no layers case).
  useEffect(() => {
    const unsub = bus.subscribeSessionState((p) => {
      setLayers(p.loaded_layers ?? []);
    });
    return unsub;
  }, [bus]);

  // Dev-only debug seam — exposes the buses to the browser console so a
  // local-dev verifier can inject session-state / map-command /
  // pipeline-state envelopes without an agent. Wrapped in
  // import.meta.env.DEV so production builds drop it.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectSessionState = (p) => {
      bus.pushSessionState(p);
    };
    window.__grace2InjectMapCommand = (p) => bus.pushMapCommand(p);
    // job-0125: dev seam to inject secrets-list envelopes (Playwright
    // verification path — no real WS needed).
    window.__grace2InjectSecretsList = (p) => {
      setSecrets(p.secrets ?? []);
    };
    // job-0126: dev seam to inject mode2-candidate envelopes (Playwright
    // verification path — no real WS / web_fetch needed).
    window.__grace2InjectMode2Candidate = (p) => fanoutMode2Candidate(p);
    // job-0137: dev seams to inject case-list / case-open envelopes
    // (Playwright + unit-test verification path — no real WS / Persistence
    // backend needed). Routes through the same handler the production WS
    // would route through so behavior is identical end-to-end.
    window.__grace2InjectCaseList = (p) => useCases_onCaseList(p);
    window.__grace2InjectCaseOpen = (p) => useCases_onCaseOpen(p);
    // job-0140: dev seam to inject payload-warning envelopes (Playwright
    // verification path — no real WS / agent needed).
    window.__grace2InjectPayloadWarning = (p) => {
      setPayloadWarnings((prev) => [p, ...prev]);
    };
    // __grace2InjectPipelineState is registered by Chat.tsx's GraceWs.
    return () => {
      delete window.__grace2InjectSessionState;
      delete window.__grace2InjectMapCommand;
      delete window.__grace2InjectSecretsList;
      delete window.__grace2InjectMode2Candidate;
      delete window.__grace2InjectCaseList;
      delete window.__grace2InjectCaseOpen;
      delete window.__grace2InjectPayloadWarning;
    };
  }, [bus, fanoutMode2Candidate, useCases_onCaseList, useCases_onCaseOpen]);

  // job-0125: bridge SecretsPanel callbacks to the active GraceWs. The panel
  // hands us a payload; we route it through wsRef.current. Captured secrets
  // (returned secrets-list) flow back via the onSecretsList handler above.
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

  // job-0137: the left rail (CasesPanel + LayerPanel) is now always available
  // — the CasesPanel is the entry surface even before any layers load. We
  // keep the leftCollapsed toggle so users can hide the rail entirely.
  const showLayersHamburger = leftCollapsed;
  const showChatHamburger = rightCollapsed;

  // job-0138: AuthGate full-screen gating. When the user has neither signed
  // in nor explicitly accepted anonymous mode, render the gate INSTEAD of
  // the main app shell. The map, panels, hamburgers, chat — everything —
  // is hidden behind it. We also gate on `authResolved` so we don't flash
  // the gate while Firebase is restoring a cached session.
  if (!appShouldRender) {
    return (
      <AuthGate
        onAnonymousAccept={handleAnonymousAccept}
      />
    );
  }

  // Residual signed-in / anonymous identity chip — replaces the dismissed
  // floating AuthPanel. Renders just to the left of the chat hamburger so
  // it's discoverable but unobtrusive. Includes a sign-out button that
  // returns the user to the AuthGate (kickoff item 4 + 5).
  const identityLabel: string = (() => {
    if (authUser && !authUser.isAnonymous) {
      return authUser.email ?? authUser.displayName ?? "Signed in";
    }
    if (anonymousAccepted) {
      return "Anonymous";
    }
    return "Signed in";
  })();

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

      {/* Left rail (job-0137): CasesPanel is the headline left-rail surface
          (always shown when leftCollapsed=false). LayerPanel keeps its
          existing absolute positioning (left:16, top:16, bottom:16, width:280)
          and renders as a sibling overlay. To prevent visual overlap when
          both are mounted, the CasesPanel shifts RIGHT past LayerPanel's
          width (16 + 280 + 8 = 304px from viewport left). With no layers
          loaded, CasesPanel sits at the canonical top-left (12,12). */}
      {!leftCollapsed && (
        <div
          data-testid="grace2-left-rail"
          style={{
            position: "absolute",
            top: 12,
            left: layers.length > 0 ? 304 : 12,
            zIndex: 20,
            maxHeight: "calc(100vh - 24px)",
          }}
        >
          <CasesPanel
            cases={cases}
            activeCaseId={activeCaseId}
            onCreate={() => createCase()}
            onSelect={selectCase}
            onRename={renameCase}
            onArchive={archiveCase}
            onDelete={deleteCase}
          />
        </div>
      )}
      {!leftCollapsed && layers.length > 0 && (
        <LayerPanel
          subscribeSessionState={bus.subscribeSessionState}
          subscribeMapCommand={bus.subscribeMapCommand}
          initialLayers={layers}
          onClose={collapseLeft}
        />
      )}

      {/* Right panel — always mounted (chat is the only way to request layers). */}
      {!rightCollapsed && (
        <Chat wsUrl={WS_URL} onClose={collapseRight} />
      )}

      {/* Layers hamburger — top-LEFT, same side as LayerPanel.
          Shown when layers exist but panel is user-collapsed. */}
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

      {/* Chat hamburger — top-RIGHT, same side as Chat panel. */}
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

      {/* SecretsPanel (job-0125): bottom-LEFT floating overlay; toggled by a
          key icon button next to the bottom-left LayerLegend. Mounted only
          when the user opens it (default closed). The toggle button itself
          renders unconditionally so a verifier can open it on first load. */}
      <button
        data-testid="grace2-secrets-toggle"
        aria-label={secretsPanelOpen ? "Hide API keys panel" : "Show API keys panel"}
        aria-expanded={secretsPanelOpen}
        aria-controls="grace2-secrets-panel-region"
        onClick={() => (secretsPanelOpen ? closeSecretsPanel() : openSecretsPanel())}
        style={{
          ...hamburgerBtnStyle,
          top: "auto",
          bottom: 12,
          left: 12,
        }}
        title={secretsPanelOpen ? "Hide API keys" : "Manage API keys"}
      >
        {/* Key glyph (U+1F511). Kept consistent with the muted overlay aesthetic. */}
        🔑
      </button>
      {secretsPanelOpen && (
        <div
          id="grace2-secrets-panel-region"
          style={{
            position: "absolute",
            bottom: 64,
            left: 12,
            zIndex: 25,
          }}
        >
          <SecretsPanel
            secrets={secrets}
            caseId={currentCaseId}
            onSecretAdd={handleSecretAdd}
            onSecretRevoke={handleSecretRevoke}
          />
        </div>
      )}

      {/* Identity chip (job-0138, replaces job-0123 AuthPanel): a small
          residual indicator showing the user's signed-in identity (email
          or "Anonymous") + a sign-out button that returns the app to the
          full-screen AuthGate. Position: top-right, mirroring where the
          AuthPanel used to sit so the user finds the auth controls where
          they expect them. The rightOffset moves it left of the chat
          hamburger (chat hamburger sits at right:12, width 40; we leave
          60px gap when chat is collapsed, more when chat is open). */}
      <div
        data-testid="grace2-identity-chip"
        data-auth-mode={
          authUser && !authUser.isAnonymous
            ? "authenticated"
            : "anonymous"
        }
        style={{
          position: "absolute",
          top: 12,
          right: rightCollapsed ? 60 : 380,
          background: "rgba(20,20,25,0.85)",
          border: "1px solid #444",
          borderRadius: 6,
          color: "#ccc",
          padding: "6px 10px",
          fontSize: 12,
          zIndex: 30,
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: 8,
          maxWidth: 260,
        }}
      >
        <span
          data-testid="grace2-identity-chip-label"
          style={{
            maxWidth: 160,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={identityLabel}
        >
          {identityLabel}
        </span>
        <button
          data-testid="grace2-identity-chip-signout"
          onClick={() => {
            void handleSignOut();
          }}
          aria-label="Sign out"
          style={{
            background: "rgba(40,40,50,0.9)",
            border: "1px solid #555",
            borderRadius: 4,
            color: "#ddd",
            padding: "3px 8px",
            cursor: "pointer",
            fontSize: 11,
            fontFamily: "inherit",
            lineHeight: 1.2,
          }}
        >
          Sign out
        </button>
      </div>
      {/* PersistenceChip (job-0137): renders to the LEFT of the identity
          chip so the two affordances are visually adjacent — auth identity
          + persistence state, the two things that gate whether work is
          being saved. */}
      <div
        data-testid="grace2-persistence-chip-wrap"
        style={{
          position: "absolute",
          top: 18,
          right: (rightCollapsed ? 60 : 380) + 110,
          zIndex: 30,
        }}
      >
        <PersistenceChip state={persistenceState} />
      </div>
      {/* Upgrade toast (job-0138 kickoff item 6): rendered when an
          anonymous user signs in. Auto-dismisses after a few seconds. */}
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

      {/* Theme toggle (job-0076 bundled): top-center, between the two
          hamburgers so it never collides. Click cycles light↔dark; persists
          in localStorage under grace2.theme. Sun = currently light (click to
          go dark), moon = currently dark (click to go light). z-index 30 so
          it overlays panels just like the hamburgers. */}
      <button
        data-testid="grace2-theme-toggle"
        aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
        aria-pressed={theme === "dark"}
        onClick={toggleTheme}
        style={{
          ...hamburgerBtnStyle,
          left: "50%",
          transform: "translateX(-50%)",
        }}
        title={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
      >
        {theme === "light" ? "☾" : "☀"}
      </button>

      {/* Mode 2 offer-to-add modal (job-0126): listens for `mode2-candidate`
          envelopes from the Wave 1 classifier. High-confidence → full modal;
          low-confidence → silent toast. Always mounted (its own render gating
          handles the empty case). */}
      <Mode2OfferModal
        subscribeCandidate={subscribeMode2Candidate}
        onAction={handleMode2Action}
      />

      {/* job-0127: Payload-warning gates stack as inline cards in a small
          column anchored below the chat header. Each renders its own
          options; on decide we send `tool-payload-confirmation` and pop the
          gate from the queue. Newest-first so a fresh gate is always visible. */}
      {payloadWarnings.length > 0 && (
        <div
          data-testid="payload-warning-stack"
          style={{
            position: "absolute",
            right: 16,
            top: 80,
            width: 360,
            display: "flex",
            flexDirection: "column",
            gap: 8,
            zIndex: 1000,
          }}
        >
          {payloadWarnings.map((w) => (
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
    </div>
  );
}
