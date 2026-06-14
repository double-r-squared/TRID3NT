// LIVE-VERIFY lens — Scenario 1: disabled-mode pass-through against the RUNNING
// dev server on :5173 (read-only; we do NOT touch it). Assert zero guard DOM and
// a normal app shell. Screenshot.
import { chromium } from "playwright";

const OUT = "/home/nate/Documents/GRACE-2/reports/inflight/job-0253-web-20260611/verify";
const URL = "http://localhost:5173/app";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });

await page.goto(URL, { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(2500); // let app shell mount

const guardSignin = await page.locator('[data-testid="grace2-auth-guard-signin"]').count();
const guardSignout = await page.locator('[data-testid="grace2-auth-guard-signout"]').count();
const guardPending = await page.locator('[data-testid="grace2-auth-guard-pending"]').count();
const guardWordmark = await page.locator('[data-testid="grace2-auth-guard-wordmark"]').count();
const guardGoogle = await page.locator('[data-testid="grace2-auth-guard-google"]').count();

// Probe for any data-testid containing "auth-guard" (catch-all).
const anyGuardDom = await page.evaluate(() =>
  Array.from(document.querySelectorAll("[data-testid]"))
    .map((e) => e.getAttribute("data-testid"))
    .filter((t) => t && t.includes("auth-guard"))
);

// Evidence the normal app shell is present. Probe several plausible shell anchors.
const bodyText = (await page.locator("body").innerText().catch(() => "")).slice(0, 400);
const shellProbes = {};
for (const sel of ["#root", "canvas", '[class*="map"]', "textarea", 'input[type="text"]', 'button']) {
  shellProbes[sel] = await page.locator(sel).count();
}

await page.screenshot({ path: `${OUT}/S1_disabled_live.png`, fullPage: false });

console.log(JSON.stringify({
  url: URL,
  guardSignin, guardSignout, guardPending, guardWordmark, guardGoogle,
  anyGuardDom,
  shellProbes,
  bodyTextHead: bodyText.replace(/\n/g, " | "),
  consoleErrors: consoleErrors.slice(0, 8),
}, null, 2));

await browser.close();
