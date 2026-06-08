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

import { useEffect, useMemo, useState } from "react";
import { MapView, type MapCommandSubscribeFunc } from "./Map";
import { Chat } from "./Chat";
import { LayerPanel, createLayerPanelBus } from "./LayerPanel";
import { LayerLegend } from "./components/LayerLegend";
import { GraceWs } from "./ws";
import {
  MapCommandPayload,
  PipelineStatePayload,
  ProjectLayerSummary,
  SessionStatePayload,
} from "./contracts";

// localStorage keys for panel collapse state (job-0065).
const LS_LEFT_COLLAPSED = "grace2.leftPanelCollapsed";
const LS_RIGHT_COLLAPSED = "grace2.rightPanelCollapsed";

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

  // Mount a GraceWs that routes session-state and map-command envelopes
  // into the LayerPanel bus. Chat.tsx handles pipeline-state and agent
  // messages via its own GraceWs.
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
      onError: () => {
        // Chat panel renders connection errors.
      },
    });
    ws.connect();
    return () => ws.close();
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
    // __grace2InjectPipelineState is registered by Chat.tsx's GraceWs.
    return () => {
      delete window.__grace2InjectSessionState;
      delete window.__grace2InjectMapCommand;
    };
  }, [bus]);

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
    </div>
  );
}
