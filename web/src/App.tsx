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
// `window.__grace2InjectSessionState`, `window.__grace2InjectMapCommand`,
// and (job-0026) `window.__grace2InjectPipelineState` so a local browser
// console can seed the LayerPanel + PipelineStrip without an agent —
// used for local-dev verification per the kickoff (step 8 of job-0025 and
// step 7 of job-0026).

import { useEffect, useMemo, useRef } from "react";
import { MapView } from "./Map";
import { Chat } from "./Chat";
import { LayerPanel, createLayerPanelBus } from "./LayerPanel";
import {
  PipelineStrip,
  createPipelineStripBus,
} from "./PipelineStrip";
import { GraceWs } from "./ws";
import {
  MapCommandPayload,
  PipelineStatePayload,
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
    __grace2InjectPipelineState?: (p: PipelineStatePayload) => void;
  }
}

export function App(): JSX.Element {
  const bus = useMemo(() => createLayerPanelBus(), []);
  const pipelineBus = useMemo(() => createPipelineStripBus(), []);
  // Hold the parallel GraceWs ref so PipelineStrip's cancel button can
  // reach `sendCancel` (the M1 cancel helper). Chat.tsx owns the other
  // GraceWs; both connections issue the same envelope shape — emitting
  // from either is the same M1-verified path. We deliberately route the
  // cancel through the App-level WS so a future M4 consolidation (drop the
  // duplicate GraceWs from Chat.tsx) is a single-file change.
  const wsRef = useRef<GraceWs | null>(null);

  // Mount a parallel GraceWs that routes session-state, map-command, and
  // pipeline-state envelopes into the panel buses. The Chat panel keeps
  // its own GraceWs (M1 contract) untouched per kickoff frozen list.
  useEffect(() => {
    const ws = new GraceWs(WS_URL, {
      onStatus: () => {
        // status indicator is on the Chat panel; we do not duplicate it.
      },
      onAgentChunk: () => {
        // Chat panel handles agent message rendering.
      },
      onPipelineState: (p) => {
        pipelineBus.pushPipelineState(p);
      },
      onSessionState: (p) => {
        bus.pushSessionState(p);
        pipelineBus.pushSessionState(p);
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
  }, [bus, pipelineBus]);

  // Dev-only debug seam — exposes the buses to the browser console so a
  // local-dev verifier can inject session-state / map-command /
  // pipeline-state envelopes without an agent. Wrapped in
  // import.meta.env.DEV so production builds drop it.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectSessionState = (p) => {
      bus.pushSessionState(p);
      pipelineBus.pushSessionState(p);
    };
    window.__grace2InjectMapCommand = (p) => bus.pushMapCommand(p);
    window.__grace2InjectPipelineState = (p) => pipelineBus.pushPipelineState(p);
    return () => {
      delete window.__grace2InjectSessionState;
      delete window.__grace2InjectMapCommand;
      delete window.__grace2InjectPipelineState;
    };
  }, [bus, pipelineBus]);

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

      {/* PipelineStrip mounted into the reserved bottom slot job-0025
          published. Subscribes to BOTH pipeline-state (step list) and
          session-state (current_pipeline). Cancel button emits via the
          M1-verified GraceWs.sendCancel path — same envelope as Chat. */}
      <PipelineStrip
        subscribePipelineState={pipelineBus.subscribePipelineState}
        subscribeSessionState={pipelineBus.subscribeSessionState}
        onCancel={(reason) => wsRef.current?.sendCancel(reason)}
      />
    </div>
  );
}
