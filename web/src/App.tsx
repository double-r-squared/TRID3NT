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
import { AuthPanel } from "./components/AuthPanel";
import { SecretsPanel } from "./components/SecretsPanel";
import {
  Mode2OfferAction,
  Mode2OfferModal,
} from "./components/Mode2OfferModal";
import { PayloadWarningInline } from "./components/PayloadWarningInline";
import { AuthUser, onAuthChanged } from "./auth";
import { GraceWs } from "./ws";
import { Mode2CandidatePayload } from "./lib/mode2_suppression";
import {
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
  // so AuthPanel re-renders and any future auth-gated UI (Cases, share links)
  // can be driven off this single source. When Firebase is unconfigured
  // (anonymous-only dev) the subscription resolves to null once and stays.
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  useEffect(() => {
    const unsub = onAuthChanged((u) => setAuthUser(u));
    return unsub;
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
  // Forward-looking: when the Case-UX shell (sibling Wave-2 job) lands a
  // currentCaseId selector, wire it here. Until then secrets are user-level
  // (case_id=null) only — surfaced as OQ-0125-CASE-ID-WIRING.
  const currentCaseId: string | null = null;

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
    // __grace2InjectPipelineState is registered by Chat.tsx's GraceWs.
    return () => {
      delete window.__grace2InjectSessionState;
      delete window.__grace2InjectMapCommand;
      delete window.__grace2InjectSecretsList;
      delete window.__grace2InjectMode2Candidate;
    };
  }, [bus, fanoutMode2Candidate]);

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

  // Whether to show the left panel:
  //   - layers must be present (no layers → no panel, no hamburger)
  //   - and user must not have collapsed it
  const showLeftPanel = layers.length > 0 && !leftCollapsed;
  // Hamburger is shown when layers exist but panel is collapsed by user.
  const showLayersHamburger = layers.length > 0 && leftCollapsed;
  const showChatHamburger = rightCollapsed;

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

      {/* Left panel — conditionally mounted: only when layers exist AND not
          collapsed. When no layers loaded, neither panel nor hamburger renders
          (per user direction: "hide layers panel until something is loaded"). */}
      {showLeftPanel && (
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

      {/* AuthPanel (job-0123): top-right, to the LEFT of the chat hamburger /
          chat panel edge so it never overlaps. Always mounted (signed-in or
          signed-out variants render differently). The rightOffset moves it
          left of the chat hamburger (chat hamburger sits at right:12, width
          40; we leave 60px gap when chat is collapsed, and more when chat is
          open since the chat panel itself takes the right edge). */}
      <AuthPanel rightOffset={rightCollapsed ? 60 : 380} />
      {/* hidden marker so tests can assert App subscribes to auth changes */}
      <span
        data-testid="grace2-app-auth-state"
        data-auth-uid={authUser?.uid ?? ""}
        data-auth-anonymous={authUser?.isAnonymous ? "true" : "false"}
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
