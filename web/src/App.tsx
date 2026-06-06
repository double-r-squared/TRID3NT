// GRACE-2 web — top-level shell for the M1 stub.
//
// Renders the MapLibre CONUS basemap full-screen with the chat panel docked
// top-right. No layer panel, no scrubber, no pick-modes (M3/M9).

import { MapView } from "./Map";
import { Chat } from "./Chat";

// WebSocket endpoint — local agent (job-0015) defaults to ws://localhost:8765.
// Override at build time with VITE_GRACE2_WS_URL (Vite picks env vars at
// build/dev start).
const WS_URL: string =
  (import.meta.env.VITE_GRACE2_WS_URL as string | undefined) ??
  "ws://localhost:8765";

export function App(): JSX.Element {
  return (
    <div style={{ position: "fixed", inset: 0 }}>
      <MapView />
      <Chat wsUrl={WS_URL} />
    </div>
  );
}
