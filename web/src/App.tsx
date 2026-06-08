// GRACE-2 web — top-level shell.
//
// M3 layout (job-0025, job-0064, job-0065):
//
//   +-----------------------------------------------------------+
//   |                                                           |
//   |   [LayerPanel] (left, 280px)            [Chat] (right)    |
//   |                                                           |
//   |                       Map (full bleed)                    |
//   |                                                           |
//   +-----------------------------------------------------------+
//
// LayerPanel and Chat float over the full-bleed map. Both panels support
// collapse/expand toggles (job-0065).
//
// job-0064 (Option A): PipelineStrip deleted. Pipeline cards now render
// inline in the Chat stream (FR-WC-8). The basemap stays clean. The
// cancel button lives in Chat's footer (FR-WC-9; Invariant 8).
//
// Subscription wiring: the LayerPanel consumes session-state +
// map-command envelopes via a local in-process bus (LayerPanelBus). The
// App connects a GraceWs instance for session-state + map-command routing.
// Chat.tsx owns its own GraceWs and handles pipeline-state, agent messages,
// and errors directly.
//
// Dev-only debug seam: in dev mode the App attaches
// `window.__grace2InjectSessionState`, `window.__grace2InjectMapCommand`,
// and `window.__grace2InjectPipelineState` (pipeline injection is wired by
// Chat.tsx via its own GraceWs handler) so a local browser console can
// seed components without an agent.

import { useEffect, useMemo, useState } from "react";
import { MapView } from "./Map";
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

/** Width of a collapsed panel strip (px) — wide enough for the chevron button. */
const COLLAPSED_WIDTH = 28;

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

export function App(): JSX.Element {
  const bus = useMemo(() => createLayerPanelBus(), []);

  // Collapse toggles (job-0065) — initialised from localStorage so reloads
  // remember the user's preference.
  const [leftCollapsed, setLeftCollapsed] = useState<boolean>(() =>
    readCollapsed(LS_LEFT_COLLAPSED),
  );
  const [rightCollapsed, setRightCollapsed] = useState<boolean>(() =>
    readCollapsed(LS_RIGHT_COLLAPSED),
  );

  // Current layer list — tracked here so LayerLegend can read it (job-0065).
  const [layers, setLayers] = useState<ProjectLayerSummary[]>([]);

  function toggleLeft(): void {
    setLeftCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(LS_LEFT_COLLAPSED, String(next));
      } catch { /* storage unavailable; non-fatal */ }
      return next;
    });
  }

  function toggleRight(): void {
    setRightCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(LS_RIGHT_COLLAPSED, String(next));
      } catch { /* storage unavailable; non-fatal */ }
      return next;
    });
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
      onError: () => {
        // Chat panel renders connection errors.
      },
    });
    ws.connect();
    return () => ws.close();
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

  // Shared chevron button style for both panel collapse toggles (job-0065).
  const chevronBtnStyle: React.CSSProperties = {
    position: "absolute",
    top: "50%",
    transform: "translateY(-50%)",
    background: "rgba(20,20,25,0.85)",
    border: "1px solid #444",
    borderRadius: 4,
    color: "#aaa",
    width: 22,
    height: 36,
    padding: 0,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 12,
    zIndex: 20,
    lineHeight: 1,
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        flexDirection: "row",
      }}
    >
      {/* Left panel slot — collapses to thin strip (job-0065). */}
      <div
        data-testid="grace2-left-panel-slot"
        style={{
          position: "relative",
          flexShrink: 0,
          width: leftCollapsed ? COLLAPSED_WIDTH : 296, // 280 panel + 16 margin
          transition: "width 0.2s ease",
          zIndex: 10,
        }}
      >
        {/* LayerPanel renders itself absolutely; slot gives it a clipping context. */}
        {!leftCollapsed && (
          <LayerPanel
            subscribeSessionState={bus.subscribeSessionState}
            subscribeMapCommand={bus.subscribeMapCommand}
            onLayersChange={setLayers}
          />
        )}
        {/* Collapse / expand chevron — on the inward (right) edge of the left slot. */}
        <button
          data-testid="grace2-left-collapse-toggle"
          aria-label={leftCollapsed ? "Expand layer panel" : "Collapse layer panel"}
          onClick={toggleLeft}
          style={{ ...chevronBtnStyle, right: -11 }}
        >
          {leftCollapsed ? "›" : "‹"}
        </button>
      </div>

      {/* Map area — grows to fill space released by collapsed panels. */}
      <div
        data-testid="grace2-map-area"
        style={{ position: "relative", flex: 1, overflow: "hidden" }}
      >
        {/* Full-bleed map */}
        <MapView />

        {/* Layer legend — bottom-center of the map area (job-0065). */}
        <LayerLegend layers={layers} />
      </div>

      {/* Right panel slot — collapses to thin strip (job-0065). */}
      <div
        data-testid="grace2-right-panel-slot"
        style={{
          position: "relative",
          flexShrink: 0,
          width: rightCollapsed ? COLLAPSED_WIDTH : 340, // Chat default width
          transition: "width 0.2s ease",
          zIndex: 10,
          overflow: "hidden",
        }}
      >
        {/* Chat panel — owns inline pipeline cards + cancel button (job-0064).
            PipelineStrip removed; basemap is now clear per user direction. */}
        {!rightCollapsed && <Chat wsUrl={WS_URL} />}
        {/* Collapse / expand chevron — on the inward (left) edge of the right slot. */}
        <button
          data-testid="grace2-right-collapse-toggle"
          aria-label={rightCollapsed ? "Expand chat panel" : "Collapse chat panel"}
          onClick={toggleRight}
          style={{ ...chevronBtnStyle, left: -11 }}
        >
          {rightCollapsed ? "‹" : "›"}
        </button>
      </div>
    </div>
  );
}
