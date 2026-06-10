// job-0264 — UI-only screenshot capture via the dev-seam injection.
// Drives the LIVE Vite dev server (:5173) through the __grace2Inject* seams
// (allowed for UI-only snapshots per kickoff). No Gemini, no agent WS — the
// seams push synthetic envelopes straight into the component bus.
//
// Captures:
//   1. layer-panel-polished.png   — the polished LayerPanel (5 kind chips)
//   2. layer-panel-hover.png      — a row with the opacity slider revealed
//   3. pipeline-cards-timers.png  — running ticker + completed/failed durations

import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.GRACE2_WEB_URL || "http://localhost:5173";

const iso = (s) => new Date(Date.now() - s * 1000).toISOString().replace(/\.\d+Z$/, "Z");

const LAYERS = [
  { layer_id: "l-flood", name: "Storm-surge max depth (Hurricane Ian)", layer_type: "raster", uri: "gs://grace-2/runs/flood/depth.cog.tif", visible: true, opacity: 0.85, z_index: 5, style_preset: "flood_depth" },
  { layer_id: "l-hill", name: "Hillshade", layer_type: "raster", uri: "gs://grace-2/runs/hill.tif", visible: true, opacity: 0.6, z_index: 4, style_preset: "hillshade" },
  { layer_id: "l-damage", name: "Building damage states (HAZUS)", layer_type: "vector", uri: "gs://grace-2/runs/dmg.fgb", visible: false, opacity: 0.9, z_index: 3, style_preset: "pelicun_damage_state" },
  { layer_id: "l-species", name: "Species occurrences", layer_type: "vector", uri: "gs://grace-2/runs/gbif.fgb", visible: true, opacity: 1, z_index: 2, style_preset: "gbif_occurrences" },
  { layer_id: "l-admin", name: "County boundaries (TIGER 2024)", layer_type: "vector", uri: "gs://grace-2/runs/admin.fgb", visible: true, opacity: 0.75, z_index: 1, style_preset: "admin_boundaries" },
];

const PIPELINE = {
  pipeline_id: "01JCAPTUREPIPELINE0000000000",
  steps: [
    { step_id: "s1", name: "fetch_dem", tool_name: "fetch_dem", state: "complete", started_at: iso(120), completed_at: iso(112), duration_ms: 8000 },
    { step_id: "s2", name: "fetch_landcover", tool_name: "fetch_landcover", state: "failed", started_at: iso(110), completed_at: iso(105), duration_ms: 5000, error_code: "UPSTREAM_API_ERROR", error_message: "NLCD WMS 503" },
    { step_id: "s3", name: "run_model_flood_scenario", tool_name: "run_solver", state: "running", started_at: iso(154) },
  ],
};

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  await page.goto(BASE, { waitUntil: "networkidle" });

  // Open a synthetic Case so the CaseView + LayerPanel mount, then inject layers.
  await page.evaluate((layers) => {
    const now = new Date().toISOString();
    const caseSummary = {
      case_id: "01JCAPTURECASE000000000000",
      title: "Hurricane Ian — Fort Myers",
      created_at: now, updated_at: now, status: "active",
    };
    window.__grace2InjectCaseList?.({ cases: [caseSummary] });
    window.__grace2InjectCaseOpen?.({ case: caseSummary, chat_history: [], loaded_layers: layers, pipeline_history: [] });
    window.__grace2InjectSessionState?.({ loaded_layers: layers });
  }, LAYERS);

  await page.waitForSelector("[data-testid='grace2-layer-panel']", { timeout: 8000 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(__dirname, "layer-panel-polished.png") });

  // Hover a row to reveal the opacity slider.
  const row = page.locator("[data-testid='layer-row']").first();
  await row.hover();
  await page.waitForTimeout(350);
  await page.screenshot({ path: join(__dirname, "layer-panel-hover.png") });

  // Inject pipeline-state for the tool-timer cards (running ticker + durations).
  await page.evaluate((pipeline) => {
    window.__grace2InjectPipelineState?.(pipeline);
  }, PIPELINE);
  await page.waitForTimeout(1600); // let the running ticker advance a couple ticks
  await page.screenshot({ path: join(__dirname, "pipeline-cards-timers.png") });

  // Tight crop of just the layer panel for a clean close-up.
  const panel = page.locator("[data-testid='grace2-layer-panel']");
  if (await panel.count()) {
    await panel.screenshot({ path: join(__dirname, "layer-panel-crop.png") });
  }

  await browser.close();
  console.log("captured screenshots to", __dirname);
}

main().catch((e) => { console.error(e); process.exit(1); });
