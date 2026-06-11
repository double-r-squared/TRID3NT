// job-0280 — static UI screenshots (component-state only; NO Gemini, NO
// chat prompts). Pipeline-state injection via __grace2InjectPipelineState is
// the blessed dev seam for component-state screenshots (and ONLY that — it
// is invalid for e2e verification per feedback_playwright_must_drive_live_agent).
//
// Run: node reports/inflight/job-0280-web-20260611/evidence/snapshot_0280.mjs
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(
  path.resolve(__dirname, "../../../../web/package.json"),
);
const { chromium } = require("playwright");

const BASE_URL = process.env.GRACE2_WEB_URL ?? "http://localhost:5173";
const OUT = __dirname;

const shot = (page, name) =>
  page.screenshot({ path: path.join(OUT, name), fullPage: false });

const RUNNING_SNAP = () => ({
  pipeline_id: "01SNAPSHOT0280PIPELINE0001",
  steps: [
    {
      step_id: "01SNAPSHOT0280STEP00000001",
      name: "fetch_3dep_dem",
      tool_name: "fetch_3dep_dem",
      state: "running",
      started_at: new Date(Date.now() - 83_000).toISOString(),
    },
  ],
});

const COMPLETE_SNAP = () => ({
  pipeline_id: "01SNAPSHOT0280PIPELINE0001",
  steps: [
    {
      step_id: "01SNAPSHOT0280STEP00000001",
      name: "fetch_3dep_dem",
      tool_name: "fetch_3dep_dem",
      state: "complete",
      started_at: new Date(Date.now() - 83_000).toISOString(),
      duration_ms: 84_200,
    },
  ],
});

async function main() {
  const browser = await chromium.launch({ headless: true });

  // ---- Mobile (390x844, iPhone-ish) ---- //
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  await ctx.addInitScript(() => {
    localStorage.setItem("grace2_anonymous_accepted", "true");
  });
  const page = await ctx.new_page?.() ?? await ctx.newPage();
  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await page.waitForSelector(
    '[data-testid="grace2-chat"][data-sheet-state="collapsed"]',
    { timeout: 15_000 },
  );
  await page.waitForTimeout(800); // map paint settles

  // 1 — collapsed sheet, nothing running → NO strip.
  if (await page.$('[data-testid="grace2-sheet-tool-strip"]')) {
    throw new Error("strip rendered with no running step");
  }
  await shot(page, "s1_sheet_collapsed_no_strip.png");

  // 2 — inject a RUNNING pipeline-state → strip above the composer.
  await page.evaluate((snap) => {
    window.__grace2InjectPipelineState(snap);
  }, RUNNING_SNAP());
  await page.waitForSelector('[data-testid="grace2-sheet-tool-strip"]', {
    timeout: 5_000,
  });
  await page.waitForTimeout(1_200); // let the timer tick once
  await shot(page, "s2_sheet_collapsed_with_strip.png");

  // 3 — tap the strip → sheet expands; the full PipelineCard is visible.
  await page.click('[data-testid="grace2-sheet-tool-strip"]');
  await page.waitForSelector(
    '[data-testid="grace2-chat"][data-sheet-state="expanded"]',
    { timeout: 5_000 },
  );
  await page.waitForSelector('[data-testid="pipeline-card"]', {
    timeout: 5_000,
  });
  await shot(page, "s3_sheet_expanded_after_strip_tap.png");

  // 4 — collapse again, inject the TERMINAL state → strip hidden.
  await page.click('[data-testid="grace2-chat-sheet-toggle"]');
  await page.waitForSelector(
    '[data-testid="grace2-chat"][data-sheet-state="collapsed"]',
    { timeout: 5_000 },
  );
  await page.evaluate((snap) => {
    window.__grace2InjectPipelineState(snap);
  }, COMPLETE_SNAP());
  await page.waitForTimeout(400);
  if (await page.$('[data-testid="grace2-sheet-tool-strip"]')) {
    throw new Error("strip still rendered after terminal pipeline-state");
  }
  await shot(page, "s4_sheet_collapsed_strip_hidden_after_complete.png");

  // 5 — drawer view (sleekness pass: panels laid into the drawer surface).
  await page.click('[data-testid="grace2-mobile-drawer-button"]');
  await page.waitForSelector('[data-testid="grace2-mobile-drawer"]', {
    timeout: 5_000,
  });
  await page.waitForTimeout(400);
  await shot(page, "s5_drawer.png");
  await ctx.close();

  // ---- Desktop (1280x800) sanity: layout unchanged, no sheet/strip ---- //
  const dctx = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });
  await dctx.addInitScript(() => {
    localStorage.setItem("grace2_anonymous_accepted", "true");
  });
  const dpage = await dctx.newPage();
  await dpage.goto(BASE_URL, { waitUntil: "networkidle" });
  await dpage.waitForSelector('[data-testid="grace2-chat"]', {
    timeout: 15_000,
  });
  await dpage.waitForTimeout(800);
  const sheetState = await dpage.getAttribute(
    '[data-testid="grace2-chat"]',
    "data-sheet-state",
  );
  if (sheetState !== null) throw new Error("desktop chat has sheet state");
  // Even with a running step injected, NO strip on desktop.
  await dpage.evaluate((snap) => {
    window.__grace2InjectPipelineState(snap);
  }, RUNNING_SNAP());
  await dpage.waitForTimeout(400);
  if (await dpage.$('[data-testid="grace2-sheet-tool-strip"]')) {
    throw new Error("strip rendered on DESKTOP");
  }
  await shot(dpage, "s6_desktop_unchanged.png");
  await dctx.close();

  await browser.close();
  console.log("OK — 6 screenshots written to", OUT);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
