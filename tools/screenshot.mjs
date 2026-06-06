#!/usr/bin/env node
// GRACE-2 — Playwright screenshot CLI (job-0027).
//
// Closes job-0016 OQ-W-3 (Chromium provisioning on Debian dev box) and
// implements the AFK iteration loop pattern from
// `feedback_playwright_afk_iteration_loop.md`: the orchestrator runs
// `make screenshot` / `make ui-tour`, then ships the resulting PNGs to
// the user's phone via SendUserFile(status='proactive').
//
// Usage:
//   node tools/screenshot.mjs [options]
//
// Options:
//   --url=<url>       Page URL (default: http://localhost:5173)
//   --route=<path>    Path appended to baseURL when --url is omitted
//                     (default: "/" — joined to http://localhost:5173)
//   --out=<path>      Output PNG path (default:
//                     /tmp/grace2-shots/<state>-<browser>-<ts>.png)
//   --state=<name>    UI state to capture. Recognized names drive any
//                     state-specific waits / interactions documented in
//                     the STATE_HOOKS table below. Unknown names are
//                     treated as "initial" with the name preserved in
//                     the output filename. Default: "initial".
//   --browser=<name>  chromium | firefox  (default: chromium)
//   --wait=<ms>       Extra delay after load before snapshot
//                     (default: 1500ms — covers MapLibre first paint).
//   --viewport=<wxh>  Viewport (default: 1440x900).
//   --full-page       Capture the full scroll-height page (default: viewport).
//
// Recognized --state values (mirror the ui-tour list):
//   initial                — baseline first paint
//   after-message          — chat panel populated (best-effort: job-0025/0026 land the controls)
//   layer-panel-open       — LayerPanel expanded (best-effort, FR-WC-4)
//   pipeline-running       — PipelineStrip rendering running steps (best-effort, FR-WC-8)
//   cancelled              — PipelineStrip rendering a cancelled step (best-effort, FR-WC-9)
//   disconnected           — connection status disconnected (best-effort)
//
// "best-effort" states warn-and-fall-back to the initial capture if the
// driving selectors are not yet present in the DOM — by design, so the
// tool ships now (job-0027) and is reused as 0025/0026 land selectors.

const USAGE = `Usage: node tools/screenshot.mjs [options]

Options:
  --url=<url>       Page URL (default: http://localhost:5173)
  --route=<path>    Path appended to baseURL when --url is omitted (default: /)
  --out=<path>      Output PNG path
                    (default: /tmp/grace2-shots/<state>-<browser>-<ts>.png)
  --state=<name>    UI state to capture:
                      initial | after-message | layer-panel-open
                      | pipeline-running | cancelled | disconnected
                    (default: initial)
  --browser=<name>  chromium | firefox (default: chromium)
  --wait=<ms>       Extra delay after load before snapshot
                    (default: 1500 — covers MapLibre first WebGL paint)
  --viewport=<wxh>  Viewport size (default: 1440x900)
  --full-page       Capture full scroll-height page (default: viewport only)
  --help, -h        Print this help and exit

Examples:
  node tools/screenshot.mjs --state=initial --out=/tmp/initial.png
  node tools/screenshot.mjs --browser=firefox --route=/
  make screenshot ROUTE=/ STATE=initial OUT=/tmp/grace2-shots/initial.png
`;

// Resolve @playwright/test out of web/node_modules. The CLI lives in
// tools/, not under web/, so Node ESM's bare-specifier walk does NOT
// find the package — we point it at the explicit ESM entrypoint instead
// (web/ is the only package.json that depends on it; root has no
// package.json, by design).
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PLAYWRIGHT_ENTRY = resolve(
  __dirname,
  "..",
  "web",
  "node_modules",
  "@playwright",
  "test",
  "index.mjs",
);
if (!existsSync(PLAYWRIGHT_ENTRY)) {
  console.error(
    `[screenshot] @playwright/test not installed at ${PLAYWRIGHT_ENTRY}; run \`make playwright-install\` first`,
  );
  process.exit(2);
}
const { chromium, firefox } = await import(PLAYWRIGHT_ENTRY);

const DEFAULTS = {
  url: "http://localhost:5173",
  route: "/",
  state: "initial",
  browser: "chromium",
  wait: 1500,
  viewport: "1440x900",
  fullPage: false,
};

function parseArgs(argv) {
  const out = { ...DEFAULTS };
  for (const raw of argv.slice(2)) {
    if (raw === "-h" || raw === "--help") {
      out.help = true;
      continue;
    }
    const m = raw.match(/^--([a-zA-Z][\w-]*)(?:=(.*))?$/);
    if (!m) continue;
    const [, key, val] = m;
    const k = key.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    if (val === undefined) out[k] = true;
    else out[k] = val;
  }
  return out;
}

function parseViewport(s) {
  const m = String(s).match(/^(\d+)x(\d+)$/);
  if (!m) throw new Error(`bad --viewport: ${s} (want WIDTHxHEIGHT, e.g. 1440x900)`);
  return { width: Number(m[1]), height: Number(m[2]) };
}

function defaultOut(state, browser) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  return join("/tmp/grace2-shots", `${state}-${browser}-${ts}.png`);
}

// State-specific driving hooks. Each receives the Locator-bearing `page`
// and returns when the state is reached (or gives up gracefully). Job-0025
// and job-0026 will land the DOM selectors these query; until then they
// fall back to a logged warning and the initial capture.
const STATE_HOOKS = {
  async initial(_page) {
    // No-op; the default --wait covers MapLibre's first WebGL paint.
  },
  async "after-message"(page) {
    const input = page.locator(
      'textarea, [data-testid="chat-input"], input[type="text"]'
    ).first();
    if (await input.count()) {
      try {
        await input.fill("hello from screenshot.mjs");
        await page.keyboard.press("Meta+Enter").catch(() => undefined);
        await page.keyboard.press("Control+Enter").catch(() => undefined);
        await page.waitForTimeout(800);
      } catch (err) {
        console.warn(`[screenshot] after-message driver failed: ${err.message}`);
      }
    } else {
      console.warn("[screenshot] no chat input found; falling back to initial");
    }
  },
  async "layer-panel-open"(page) {
    const btn = page.locator(
      '[data-testid="layer-panel-toggle"], button:has-text("Layers")'
    ).first();
    if (await btn.count()) {
      try {
        await btn.click();
        await page.waitForTimeout(500);
      } catch (err) {
        console.warn(`[screenshot] layer-panel-open driver failed: ${err.message}`);
      }
    } else {
      console.warn(
        "[screenshot] LayerPanel toggle not found (job-0025 lands the selector); falling back to initial"
      );
    }
  },
  async "pipeline-running"(page) {
    // PipelineStrip lands in job-0026 — selector reserved.
    console.warn(
      "[screenshot] pipeline-running requires job-0026 PipelineStrip; capturing initial frame"
    );
    await page.waitForTimeout(200);
  },
  async cancelled(page) {
    console.warn(
      "[screenshot] cancelled requires job-0026 PipelineStrip + cancel chain; capturing initial frame"
    );
    await page.waitForTimeout(200);
  },
  async disconnected(page) {
    // Disconnected state requires no agent on ws://localhost:8765, which
    // is the default for this M3 capture session (no agent in-flight).
    await page.waitForTimeout(500);
  },
};

async function main() {
  const args = parseArgs(process.argv);
  if (args.help) {
    process.stdout.write(USAGE);
    process.exit(0);
  }
  const browserName = String(args.browser);
  if (!["chromium", "firefox"].includes(browserName)) {
    console.error(`bad --browser: ${browserName} (chromium|firefox)`);
    process.exit(2);
  }
  const viewport = parseViewport(args.viewport);
  const url = args.url || `http://localhost:5173${args.route.startsWith("/") ? args.route : "/" + args.route}`;
  const out = args.out || defaultOut(args.state, browserName);
  const waitMs = Number(args.wait);
  const fullPage = !!args.fullPage;

  await mkdir(dirname(out), { recursive: true });

  const launcher = browserName === "firefox" ? firefox : chromium;
  console.log(`[screenshot] browser=${browserName} url=${url} state=${args.state} out=${out}`);
  const browser = await launcher.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport,
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();
    page.on("pageerror", (err) =>
      console.warn(`[screenshot] pageerror: ${err.message}`)
    );
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        console.warn(`[screenshot] console.error: ${msg.text()}`);
      }
    });

    const resp = await page.goto(url, { waitUntil: "load", timeout: 30000 });
    if (!resp || !resp.ok()) {
      const status = resp ? resp.status() : "no-response";
      console.warn(`[screenshot] navigation status=${status} (continuing)`);
    }
    await page.waitForTimeout(waitMs);

    const hook = STATE_HOOKS[args.state];
    if (hook) {
      await hook(page);
    } else {
      console.warn(`[screenshot] unknown --state=${args.state}; using initial`);
    }

    await page.screenshot({ path: out, fullPage });
    if (!existsSync(out)) {
      throw new Error(`screenshot file missing after capture: ${out}`);
    }
    console.log(`[screenshot] wrote ${out}`);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
