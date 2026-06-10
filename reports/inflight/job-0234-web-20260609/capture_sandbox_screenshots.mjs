#!/usr/bin/env node
// GRACE-2 — Playwright snapshot for job-0234 SandboxCard.
// Injects sandbox state via __grace2InjectCodeExec dev seam and captures:
//   01_sandbox_request_gate.png — REQUEST state showing code + Proceed/Cancel
//   02_sandbox_ok_result.png    — RESULT state (ok, scalar result)
//   03_sandbox_blocked.png      — RESULT state (blocked status)
//
// Usage: node capture_sandbox_screenshots.mjs
// Requires: Vite dev server running on http://localhost:5173

import pkg from "/home/nate/Documents/GRACE-2/web/node_modules/playwright/index.js";
const { chromium } = pkg;
import { mkdir } from "fs/promises";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const EVIDENCE_DIR = join(__dirname, "evidence");
const BASE_URL = "http://localhost:5173";
const WAIT_MS = 2000;

await mkdir(EVIDENCE_DIR, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });

// Pre-set localStorage so AuthGate passes without real Firebase auth.
// grace2_anonymous_accepted="true" causes readAnonymousAccepted() to return true
// so the gate condition: authResolved && (authenticated || anonymousAccepted) passes
// once Firebase's initAuth resolves (even to null).
await ctx.addInitScript(() => {
  localStorage.setItem("grace2_anonymous_accepted", "true");
});

// ---------------------------------------------------------------------------
// Helper: open page + wait for chat panel
// ---------------------------------------------------------------------------
async function openPage() {
  const page = await ctx.newPage();
  await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(WAIT_MS);
  // Chat panel may be collapsed — open it via the hamburger if needed
  const chatMount = page.locator('[data-testid="grace2-chat-mount"]').first();
  const isMounted = await chatMount.count() > 0;
  if (!isMounted) {
    // Try hamburger button to expand
    const hamburger = page.locator('[data-testid="grace2-chat-hamburger"]').first();
    if (await hamburger.count() > 0) {
      await hamburger.click();
      await page.waitForTimeout(500);
    }
  } else {
    const chatHidden = await chatMount.evaluate((el) =>
      el.style.display === "none" || el.getAttribute("aria-hidden") === "true"
    ).catch(() => false);
    if (chatHidden) {
      const hamburger = page.locator('[data-testid="grace2-chat-hamburger"]').first();
      if (await hamburger.count() > 0) {
        await hamburger.click();
        await page.waitForTimeout(500);
      }
    }
  }
  // Wait for the chat scroll to be visible inside the mount
  await page.waitForSelector('[data-testid="chat-scroll"]', { timeout: 15000 });
  return page;
}

// ---------------------------------------------------------------------------
// Screenshot 1: REQUEST state — gate (no decision yet)
// ---------------------------------------------------------------------------
{
  const page = await openPage();

  const REQUEST = {
    envelope_type: "code-exec-request",
    code_exec_id: "01J0000000000000000000001A",
    python_code:
      "import numpy as np\nimport rasterio\n\n# Compute 95th percentile flood depth\nwith rasterio.open(flood_depth) as src:\n    data = src.read(1)\n    valid = data[data > 0]\n\nresult = float(np.percentile(valid, 95))\nprint(f'p95 flood depth: {result:.2f} m')",
    layer_refs: { flood_depth: "gs://grace-2-hazard-prod-runs/layers/flood-depth-202506.tif" },
    rationale: "Computing the 95th-percentile flood depth over the inundated area",
  };

  await page.evaluate((req) => {
    if (typeof window.__grace2InjectCodeExec === "function") {
      window.__grace2InjectCodeExec({ request: req });
    } else {
      console.error("__grace2InjectCodeExec not found — DEV mode required");
    }
  }, REQUEST);

  await page.waitForTimeout(800);
  await page.waitForSelector('[data-testid="sandbox-card"]', { timeout: 5000 });
  // Scroll the chat scroll into view of the card
  await page.evaluate(() => {
    const el = document.querySelector('[data-testid="sandbox-card"]');
    el?.scrollIntoView({ behavior: "instant", block: "center" });
  });
  await page.waitForTimeout(300);

  const out1 = join(EVIDENCE_DIR, "01_sandbox_request_gate.png");
  await page.screenshot({ path: out1, fullPage: false });
  console.log("Saved:", out1);
  await page.close();
}

// ---------------------------------------------------------------------------
// Screenshot 2: RESULT state — ok (scalar result = 2.34)
// ---------------------------------------------------------------------------
{
  const page = await openPage();

  const REQUEST2 = {
    envelope_type: "code-exec-request",
    code_exec_id: "01J0000000000000000000002B",
    python_code:
      "import numpy as np\nresult = float(np.mean([1.5, 2.0, 3.02, 2.4]))",
    layer_refs: {},
    rationale: "Computing the mean of test flood depth values",
  };

  const RESULT_OK = {
    envelope_type: "code-exec-result",
    code_exec_id: "01J0000000000000000000002B",
    status: "ok",
    stdout_tail: "mean flood depth: 2.23 m",
    stderr_tail: "",
    result: { kind: "json", value: 2.23 },
    truncated: false,
    duration_s: 0.82,
  };

  await page.evaluate(({ req, res, dec }) => {
    if (typeof window.__grace2InjectCodeExec === "function") {
      window.__grace2InjectCodeExec({ request: req, result: res, decision: dec });
    }
  }, { req: REQUEST2, res: RESULT_OK, dec: "proceed" });

  await page.waitForTimeout(800);
  await page.waitForSelector('[data-testid="sandbox-card-status-chip"]', { timeout: 5000 });
  await page.evaluate(() => {
    const el = document.querySelector('[data-testid="sandbox-card"]');
    el?.scrollIntoView({ behavior: "instant", block: "center" });
  });
  await page.waitForTimeout(300);

  const out2 = join(EVIDENCE_DIR, "02_sandbox_ok_result.png");
  await page.screenshot({ path: out2, fullPage: false });
  console.log("Saved:", out2);
  await page.close();
}

// ---------------------------------------------------------------------------
// Screenshot 3: RESULT state — blocked (network egress denied)
// ---------------------------------------------------------------------------
{
  const page = await openPage();

  const REQUEST3 = {
    envelope_type: "code-exec-request",
    code_exec_id: "01J0000000000000000000003C",
    python_code:
      "import socket\nsocket.create_connection(('example.com', 80), timeout=5)\nresult = 'network reached'",
    layer_refs: {},
    rationale: "Testing network egress (should be blocked by sandbox firewall)",
  };

  const RESULT_BLOCKED = {
    envelope_type: "code-exec-result",
    code_exec_id: "01J0000000000000000000003C",
    status: "blocked",
    stdout_tail: "",
    stderr_tail:
      "SandboxNetworkBlocked: egress to example.com:80 blocked by in-process network guard.\n  Allowed: restricted.googleapis.com",
    result: null,
    truncated: false,
    duration_s: 0.04,
  };

  await page.evaluate(({ req, res, dec }) => {
    if (typeof window.__grace2InjectCodeExec === "function") {
      window.__grace2InjectCodeExec({ request: req, result: res, decision: dec });
    }
  }, { req: REQUEST3, res: RESULT_BLOCKED, dec: "proceed" });

  await page.waitForTimeout(800);
  await page.waitForSelector('[data-testid="sandbox-card-status-chip"]', { timeout: 5000 });
  // expand stderr to show the blocked message
  const stderrToggle = page.locator('[data-testid="sandbox-card-stderr-toggle"]').first();
  if (await stderrToggle.count() > 0) {
    await stderrToggle.click();
    await page.waitForTimeout(300);
  }
  await page.evaluate(() => {
    const el = document.querySelector('[data-testid="sandbox-card"]');
    el?.scrollIntoView({ behavior: "instant", block: "center" });
  });
  await page.waitForTimeout(300);

  const out3 = join(EVIDENCE_DIR, "03_sandbox_blocked_result.png");
  await page.screenshot({ path: out3, fullPage: false });
  console.log("Saved:", out3);
  await page.close();
}

await browser.close();
console.log("Done. Evidence in:", EVIDENCE_DIR);
