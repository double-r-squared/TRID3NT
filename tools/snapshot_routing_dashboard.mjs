#!/usr/bin/env node
// GRACE-2 — Routing-quality dashboard snapshot (Wave 4.11 M7).
//
// Drives the Vite dev server at http://localhost:5173/, injects a fixture
// into ``window.__grace2InjectTelemetryFixture`` before the app boots, and
// captures the rendered RoutingQualityDashboard popup to PNG.
//
// Usage:
//   node tools/snapshot_routing_dashboard.mjs [--out=path] [--url=...]

import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
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
    `[snapshot] @playwright/test not installed at ${PLAYWRIGHT_ENTRY}`,
  );
  process.exit(2);
}
const { chromium } = await import(PLAYWRIGHT_ENTRY);

const DEFAULTS = {
  url: "http://localhost:5173/",
  out: "/tmp/wave4_11_m7_dashboard.png",
  wait: 2000,
};

function parseArgs(argv) {
  const out = { ...DEFAULTS };
  for (const raw of argv.slice(2)) {
    const m = raw.match(/^--([a-zA-Z][\w-]*)(?:=(.*))?$/);
    if (!m) continue;
    const [, key, val] = m;
    if (val === undefined) out[key] = true;
    else out[key] = val;
  }
  return out;
}

// Fixture mirrors RoutingDashboardSummary in
// web/src/components/RoutingQualityDashboard.tsx
const FIXTURE = {
  total_dispatches: 142,
  session_count: 12,
  error_rate_overall: 0.085,
  cache_hit_rate: 0.62,
  average_latency_ms: 234.5,
  dispatches_by_tool: [
    { name: "fetch_dem", count: 41, error_count: 2, error_rate: 0.049, avg_latency_ms: 312.5 },
    { name: "compute_hillshade", count: 28, error_count: 0, error_rate: 0.0, avg_latency_ms: 145.0 },
    { name: "publish_layer", count: 22, error_count: 3, error_rate: 0.136, avg_latency_ms: 510.2 },
    { name: "fetch_nws_alerts_conus", count: 14, error_count: 1, error_rate: 0.071, avg_latency_ms: 88.4 },
    { name: "fetch_administrative_boundaries", count: 11, error_count: 0, error_rate: 0.0, avg_latency_ms: 198.7 },
    { name: "compute_zonal_statistics", count: 9, error_count: 0, error_rate: 0.0, avg_latency_ms: 412.8 },
    { name: "fetch_landcover_nlcd", count: 7, error_count: 1, error_rate: 0.143, avg_latency_ms: 256.1 },
    { name: "model_flood_scenario", count: 4, error_count: 1, error_rate: 0.25, avg_latency_ms: 1402.7 },
    { name: "compute_slope", count: 3, error_count: 0, error_rate: 0.0, avg_latency_ms: 188.5 },
    { name: "fetch_precip_return_period", count: 3, error_count: 1, error_rate: 0.333, avg_latency_ms: 195.0 },
  ],
  dispatches_by_source: { llm: 118, workflow: 24 },
  error_rate_by_tool: [
    { name: "fetch_dem", error_rate: 0.049, error_count: 2, total: 41 },
    { name: "publish_layer", error_rate: 0.136, error_count: 3, total: 22 },
    { name: "model_flood_scenario", error_rate: 0.25, error_count: 1, total: 4 },
  ],
  top_routing_chains: [
    { chain: ["fetch_dem", "compute_hillshade"], count: 18 },
    { chain: ["fetch_dem", "publish_layer"], count: 12 },
    { chain: ["compute_hillshade", "publish_layer"], count: 9 },
    { chain: ["fetch_administrative_boundaries", "fetch_dem"], count: 6 },
    { chain: ["fetch_landcover_nlcd", "compute_zonal_statistics"], count: 5 },
  ],
  source: "mongo",
};

async function main() {
  const args = parseArgs(process.argv);
  const url = String(args.url);
  const out = String(args.out);
  const waitMs = Number(args.wait);

  await mkdir(dirname(out), { recursive: true });

  console.log(`[snapshot] url=${url} out=${out}`);
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();
    page.on("pageerror", (err) =>
      console.warn(`[snapshot] pageerror: ${err.message}`),
    );
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        console.warn(`[snapshot] console.error: ${msg.text()}`);
      }
    });

    // Inject the fixture BEFORE the app's bundle runs so App.tsx picks it up
    // on its initial useEffect pass. Also pre-set the anonymous-accepted
    // localStorage flag so AuthGate doesn't intercept the render.
    await page.addInitScript((fixture) => {
      try {
        localStorage.setItem("grace2_anonymous_accepted", "true");
      } catch (e) {
        /* tolerated */
      }
      window.__grace2InjectTelemetryFixture = fixture;
    }, FIXTURE);

    const resp = await page.goto(url, { waitUntil: "load", timeout: 30000 });
    if (!resp || !resp.ok()) {
      const status = resp ? resp.status() : "no-response";
      console.warn(`[snapshot] navigation status=${status} (continuing)`);
    }

    // Give MapLibre + the dashboard time to render. The dashboard auto-opens
    // because App.tsx's mount effect sets routingDashOpen=true when the inject
    // fixture is present.
    await page.waitForTimeout(waitMs);

    // Wait for the dashboard popup to actually be in the DOM.
    const dash = page.locator('[data-testid="grace2-routing-dashboard"]').first();
    try {
      await dash.waitFor({ state: "visible", timeout: 8000 });
    } catch (err) {
      console.warn(`[snapshot] dashboard not visible: ${err.message}`);
    }

    await page.screenshot({ path: out, fullPage: false });
    if (!existsSync(out)) {
      throw new Error(`screenshot file missing after capture: ${out}`);
    }
    console.log(`[snapshot] wrote ${out}`);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
