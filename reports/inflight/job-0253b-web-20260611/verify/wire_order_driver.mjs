// LIVE-VERIFY proof (1): drive the REAL ws.ts (vite-compiled) in a REAL browser
// against a REAL gate-ON agent (AUTH_REQUIRED=true). Capture WS frames via CDP and
// prove the FIRST client-sent frame is `auth-token`.
//
// No inject seams, no Gemini. The browser loads the real App.tsx GraceWs; the agent
// is the real server.py with AUTH_REQUIRED=true. We read Network.webSocketFrameSent
// (client->server) and Network.webSocketFrameReceived (server->client).
import pw from "/home/nate/Documents/GRACE-2/web/node_modules/playwright/index.js";
const { chromium } = pw;

const URL = process.argv[2] || "http://127.0.0.1:5191/";
const OUT = process.argv[3] || "/tmp/wireorder";
import { writeFileSync } from "node:fs";

const sentFrames = [];   // client -> server
const recvFrames = [];   // server -> client
const wsEvents = [];

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext();
// Seed the AuthGate anonymous-accept flag so the workbench shell renders in
// disabled mode (same seam the existing live-verify tools use). This does NOT
// affect the agent's frame-order gate — it only un-gates the web AuthGate UI.
await ctx.addInitScript(() => {
  try { window.localStorage.setItem("grace2_anonymous_accepted", "1"); } catch {}
});
const page = await ctx.newPage();
const cdp = await ctx.newCDPSession(page);
await cdp.send("Network.enable");

cdp.on("Network.webSocketCreated", (e) => {
  wsEvents.push({ kind: "created", url: e.url, ts: Date.now() });
});
cdp.on("Network.webSocketFrameSent", (e) => {
  const payload = e.response?.payloadData ?? "";
  let type = "(unparsed)";
  try { type = JSON.parse(payload).type ?? "(no-type)"; } catch {}
  sentFrames.push({ type, ts: Date.now(), len: payload.length });
});
cdp.on("Network.webSocketFrameReceived", (e) => {
  const payload = e.response?.payloadData ?? "";
  let type = "(unparsed)";
  let extra = {};
  try {
    const o = JSON.parse(payload);
    type = o.type ?? "(no-type)";
    if (o.type === "error") extra = { error_code: o.payload?.error_code, message: o.payload?.message };
  } catch {}
  recvFrames.push({ type, ts: Date.now(), ...extra });
});
cdp.on("Network.webSocketClosed", () => {
  wsEvents.push({ kind: "closed", ts: Date.now() });
});

const consoleLines = [];
page.on("console", (m) => consoleLines.push(`[${m.type()}] ${m.text()}`));

await page.goto(URL, { waitUntil: "networkidle" }).catch(() => {});
// Give the WS open handler + first frames time to fly and the gate to respond.
await page.waitForTimeout(4000);

await page.screenshot({ path: `${OUT}_gateon.png`, fullPage: false }).catch(() => {});

const result = {
  url: URL,
  ws_events: wsEvents,
  client_sent_frames_in_order: sentFrames,
  server_received_frames_in_order: recvFrames,
  first_client_frame: sentFrames[0]?.type ?? null,
  console_tail: consoleLines.slice(-25),
};
writeFileSync(`${OUT}_frames.json`, JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));

await browser.close();
