// GRACE-2 web — top-level shell.
//
// M3 layout (job-0025):
//
//   +-----------------------------------------------------------+
//   |                                                           |
//   |   [LayerPanel] (left, 280px)            [Chat] (right)    |
//   |                                                           |
//   |                       Map (full bleed)                    |
//   |                                                           |
//   |   ------------- PipelineStrip slot (bottom) --------------|
//   |                                                           |
//   +-----------------------------------------------------------+
//
// LayerPanel and Chat float over the full-bleed map. The PipelineStrip
// slot at the bottom is reserved space for job-0026 — currently empty
// `null` so job-0026 only needs to import + render its own component
// inside the slot without restructuring the shell.
//
// Subscription wiring: the LayerPanel consumes session-state +
// map-command envelopes via a local in-process bus (LayerPanelBus). The
// Chat panel owns its own GraceWs (M1 contract), AND the App connects
// a second GraceWs instance to route session-state + map-command into
// the bus. This deliberate double-connection is M3-scaffold: M4 will
// consolidate when the agent emits both protocol families on the same
// connection; for M3 it keeps the M1 Chat code untouched (kickoff
// requires we do not modify Chat.tsx or substantive ws.ts logic).
//
// Dev-only debug seam: in dev mode the App attaches
// `window.__grace2InjectSessionState` and `window.__grace2InjectMapCommand`
// so a local browser console can seed the LayerPanel without an agent —
// used for local-dev verification per the kickoff (step 8).

import { useEffect, useMemo } from "react";
import { MapView } from "./Map";
import { Chat } from "./Chat";
import { LayerPanel, createLayerPanelBus } from "./LayerPanel";
import { GraceWs } from "./ws";
import {
  MapCommandPayload,
  SessionStatePayload,
} from "./contracts";

// WebSocket endpoint — local agent (job-0015) defaults to ws://localhost:8765.
// Override at build time with VITE_GRACE2_WS_URL.
const WS_URL: string =
  (import.meta.env.VITE_GRACE2_WS_URL as string | undefined) ??
  "ws://localhost:8765";

declare global {
  interface Window {
    __grace2InjectSessionState?: (p: SessionStatePayload) => void;
    __grace2InjectMapCommand?: (p: MapCommandPayload) => void;
  }
}

export function App(): JSX.Element {
  const bus = useMemo(() => createLayerPanelBus(), []);

  // Mount a parallel GraceWs that routes session-state and map-command
  // envelopes into the LayerPanel bus. The Chat panel keeps its own
  // GraceWs (M1 contract) untouched.
  useEffect(() => {
    const ws = new GraceWs(WS_URL, {
      onStatus: () => {
        // status indicator is on the Chat panel; we do not duplicate it.
      },
      onAgentChunk: () => {
        // Chat panel handles agent message rendering.
      },
      onPipelineState: () => {
        // PipelineStrip (job-0026) will consume this via the same bus.
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

  // Dev-only debug seam — exposes the bus to the browser console so a
  // local-dev verifier can inject a session-state envelope without an
  // agent. Wrapped in import.meta.env.DEV so production builds drop it.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectSessionState = (p) => bus.pushSessionState(p);
    window.__grace2InjectMapCommand = (p) => bus.pushMapCommand(p);
    return () => {
      delete window.__grace2InjectSessionState;
      delete window.__grace2InjectMapCommand;
    };
  }, [bus]);

  return (
    <div style={{ position: "fixed", inset: 0 }}>
      {/* Full-bleed map (z-index baseline) */}
      <MapView />

      {/* LayerPanel docked left (over map) */}
      <LayerPanel
        subscribeSessionState={bus.subscribeSessionState}
        subscribeMapCommand={bus.subscribeMapCommand}
      />

      {/* Chat panel docked right (M1 placement preserved) */}
      <Chat wsUrl={WS_URL} />

      {/* PipelineStrip slot — reserved for job-0026.
          Layout slot:
            position: absolute
            left: 312 (LayerPanel width 280 + 16 gap + 16 inset)
            right: 412 (Chat panel width 380 + 16 gap + 16 inset)
            bottom: 16
            height: ~96 (recommended; job-0026 owns final sizing)
          Render as null until job-0026 mounts the component here. */}
      {/* <PipelineStrip ... /> */}
    </div>
  );
}
