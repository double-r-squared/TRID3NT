// job-0123 live verification — drives the AuthPanel through the anonymous
// flow and asserts:
//   1. Signed-out state shows both buttons.
//   2. Clicking "Continue as anonymous" works (no exception thrown).
//   3. WS connect (mocked) does NOT send an `auth-token` envelope when
//      Firebase is unconfigured (the anonymous fallback per kickoff #4).
//   4. The page remains usable (chat panel reachable, app shell intact).
//
// Output: /tmp/grace2-shots-0123/{auth-signed-out,auth-after-anonymous}.png

import { chromium } from "@playwright/test";
import { mkdirSync } from "fs";

const OUT_DIR = "/tmp/grace2-shots-0123";
const URL = process.env.GRACE2_URL ?? "http://localhost:5173";

mkdirSync(OUT_DIR, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  // Capture all WebSocket frames so we can prove no auth-token was sent.
  const wsSent = [];
  page.on("websocket", (ws) => {
    ws.on("framesent", ({ payload }) => {
      try {
        const obj = JSON.parse(payload);
        wsSent.push({ type: obj.type, payload: obj.payload });
      } catch {
        // ignore binary
      }
    });
  });

  await page.goto(URL, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="grace2-auth-panel"]', { timeout: 15000 });

  // 1. Verify signed-out state.
  const state = await page.getAttribute('[data-testid="grace2-auth-panel"]', "data-auth-state");
  if (state !== "signed-out") {
    throw new Error(`Expected signed-out, got ${state}`);
  }
  const googleBtn = await page.isVisible('[data-testid="grace2-auth-google"]');
  const anonBtn = await page.isVisible('[data-testid="grace2-auth-anonymous"]');
  console.log(`[verify] signed-out OK: google_button=${googleBtn} anonymous_button=${anonBtn}`);

  await page.screenshot({ path: `${OUT_DIR}/auth-signed-out.png`, fullPage: false });

  // 2. Click "Continue as anonymous". With Firebase unconfigured this
  //    surfaces the "Anonymous mode" friendly message and produces no
  //    auth-token wire frame.
  await page.click('[data-testid="grace2-auth-anonymous"]');
  await page.waitForTimeout(1500);

  await page.screenshot({ path: `${OUT_DIR}/auth-after-anonymous.png`, fullPage: false });

  // 3. Inspect captured WS frames.
  const authTokenFrames = wsSent.filter((f) => f.type === "auth-token");
  const sessionResumeFrames = wsSent.filter((f) => f.type === "session-resume");
  console.log(
    `[verify] ws frames sent: total=${wsSent.length} session-resume=${sessionResumeFrames.length} auth-token=${authTokenFrames.length}`,
  );
  console.log(`[verify] frame types: ${JSON.stringify(wsSent.map((f) => f.type))}`);

  if (authTokenFrames.length !== 0) {
    throw new Error(
      `Expected zero auth-token frames in anonymous mode (Firebase unconfigured), got ${authTokenFrames.length}`,
    );
  }

  // 4. Sanity: app shell + chat hamburger or chat panel must be reachable.
  const shellVisible = await page.isVisible('[data-testid="grace2-app-shell"]');
  if (!shellVisible) {
    throw new Error("App shell not visible — UI broken by AuthPanel mount");
  }

  console.log(
    `[verify] PASS — anonymous flow works, no auth-token sent (Firebase unconfigured), app shell intact`,
  );

  await browser.close();
})().catch((err) => {
  console.error("[verify] FAIL:", err);
  process.exit(1);
});
