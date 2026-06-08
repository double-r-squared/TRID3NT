/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// GRACE-2 web client dev server. Host 0.0.0.0 + port 5173 so the dev
// server is reachable on the LAN for cross-browser spot checks (NFR-PO-1).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // Use polling for HMR file watching. The Debian dev host hits inotify
    // ENOSPC under typical session load (max_user_instances=128 is exhausted
    // by other long-running tools). Polling sidesteps the limit at the cost
    // of a little CPU — acceptable for a stub.
    watch: {
      usePolling: true,
      interval: 1000,
    },
  },
  test: {
    environment: "happy-dom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
