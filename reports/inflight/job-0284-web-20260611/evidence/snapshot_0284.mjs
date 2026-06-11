// job-0284 — mobile map-centric pass screenshots (component-state only; NO
// Gemini, NO chat prompts). Dev-seam injection (__grace2InjectCaseList /
// __grace2InjectCaseOpen / __grace2InjectCaseOpenChat /
// __grace2InjectPipelineState) is the blessed pattern for populated-state
// component screenshots — and ONLY that (invalid for e2e verification per
// feedback_playwright_must_drive_live_agent).
//
// Run: node reports/inflight/job-0284-web-20260611/evidence/snapshot_0284.mjs <prefix>
//   prefix = "before" | "after"
//
// Desktop shots (1440x900@2x) are the byte-compare control: this job is a
// MOBILE pass, so desktop must render pixel-identical before vs after.
// Mobile shots (390x844@2x) are the change evidence.
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
const PREFIX = process.argv[2] ?? "shot";

const shot = (page, name) =>
  page.screenshot({ path: path.join(OUT, `${PREFIX}_${name}`), fullPage: false });

const NOW = Date.now();
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

// --- Fixtures (display-only; nothing reaches an agent) ------------------- //

const LAYERS = [
  {
    layer_id: "01JOB0284LAYER000000000001",
    name: "SFINCS max inundation depth",
    layer_type: "raster",
    uri: "gs://grace2-demo/sfincs_depth.tif",
    attribution: "SFINCS",
    visible: true,
    opacity: 0.8,
    z_index: 3,
    style_preset: "flood_depth",
  },
  {
    layer_id: "01JOB0284LAYER000000000002",
    name: "USGS 3DEP hillshade",
    layer_type: "raster",
    uri: "gs://grace2-demo/hillshade.tif",
    attribution: "USGS 3DEP",
    visible: true,
    opacity: 0.55,
    z_index: 2,
    style_preset: "hillshade",
  },
  {
    layer_id: "01JOB0284LAYER000000000003",
    name: "Ada County boundary",
    layer_type: "geojson",
    uri: "gs://grace2-demo/ada_county.geojson",
    attribution: "US Census TIGER",
    visible: false,
    opacity: 1,
    z_index: 1,
    style_preset: "admin_boundaries",
  },
];

const CASES = [
  {
    case_id: "01JOB0284CASE0000000000001",
    title: "Boise River flood scenario",
    created_at: iso(86_400_000 * 3),
    updated_at: iso(3_600_000 * 2),
    status: "active",
    bbox: [-116.4, 43.5, -116.0, 43.75],
    primary_hazard: "flood",
  },
  {
    case_id: "01JOB0284CASE0000000000002",
    title: "Hurricane surge — Lee County",
    created_at: iso(86_400_000 * 9),
    updated_at: iso(86_400_000 * 1.2),
    status: "active",
    bbox: [-82.2, 26.3, -81.6, 26.8],
    primary_hazard: "hurricane",
  },
  {
    case_id: "01JOB0284CASE0000000000003",
    title: "Wildfire smoke transport study",
    created_at: iso(86_400_000 * 20),
    updated_at: iso(86_400_000 * 6),
    status: "active",
    bbox: null,
    primary_hazard: null,
  },
];

const CHAT_HISTORY = [
  {
    message_id: "01JOB0284MSG00000000000001",
    case_id: CASES[0].case_id,
    role: "user",
    content: "Model a 100-year flood for the Boise River through Ada County.",
    created_at: iso(3_600_000 * 2 + 240_000),
  },
  {
    message_id: "01JOB0284MSG00000000000002",
    case_id: CASES[0].case_id,
    role: "agent",
    content:
      "I'll fetch the 3DEP terrain for Ada County first, then run the SFINCS inundation model with the 100-year discharge.",
    created_at: iso(3_600_000 * 2 + 180_000),
  },
  {
    message_id: "01JOB0284MSG00000000000003",
    case_id: CASES[0].case_id,
    role: "tool",
    content: "{}",
    tool_card: {
      tool_name: "fetch_3dep_dem",
      state: "complete",
      started_at: iso(3_600_000 * 2 + 170_000),
      duration_ms: 42_300,
      label: "fetch_3dep_dem",
    },
    created_at: iso(3_600_000 * 2 + 120_000),
  },
  {
    message_id: "01JOB0284MSG00000000000004",
    case_id: CASES[0].case_id,
    role: "agent",
    content:
      "Terrain is loaded. The SFINCS run is underway — the max-depth raster will land on the map when it completes.",
    created_at: iso(3_600_000 * 2 + 60_000),
  },
];

const CASE_OPEN = {
  envelope_type: "case-open",
  session_state: {
    case: CASES[0],
    chat_history: CHAT_HISTORY,
    loaded_layers: LAYERS,
    pipeline_history: [],
    current_pipeline: null,
  },
};

const RUNNING_SNAP = {
  pipeline_id: "01JOB0284PIPELINE000000001",
  steps: [
    {
      step_id: "01JOB0284STEP0000000000001",
      name: "run_model_flood_scenario",
      tool_name: "run_model_flood_scenario",
      state: "running",
      started_at: iso(95_000),
    },
  ],
};

async function newPage(browser, { width, height, mobile = false, theme = "light" }) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    isMobile: mobile,
    hasTouch: mobile,
  });
  await ctx.addInitScript((t) => {
    localStorage.setItem("grace2_anonymous_accepted", "true");
    localStorage.setItem("grace2.theme", t);
  }, theme);
  const page = await ctx.newPage();
  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="grace2-chat"]', { timeout: 15_000 });
  await page.waitForTimeout(1_500); // map tiles settle
  return { ctx, page };
}

async function injectCases(page) {
  await page.evaluate((cases) => {
    window.__grace2InjectCaseList({ envelope_type: "case-list", cases });
  }, CASES);
  await page.waitForTimeout(150);
}

async function injectCaseOpen(page) {
  await page.evaluate(
    ({ cases, open }) => {
      window.__grace2InjectCaseList({ envelope_type: "case-list", cases });
      window.__grace2InjectCaseOpen(open);
      const chatSeam = window.__grace2InjectCaseOpenChat;
      if (typeof chatSeam === "function") chatSeam(open);
    },
    { cases: CASES, open: CASE_OPEN },
  );
  // Case-open replays a zoom-to (snap-to-location, job-0280/0281): give the
  // camera animation + tile fetch time to fully settle so captures are
  // deterministic (byte-compare needs an idle map).
  await page.waitForTimeout(3_500);
}

async function main() {
  const browser = await chromium.launch({ headless: true });

  // --- Desktop control (byte-compare target — mobile job must not move it) //
  for (const theme of ["light", "dark"]) {
    {
      const { ctx, page } = await newPage(browser, { width: 1440, height: 900, theme });
      await injectCases(page);
      await page.waitForSelector('[data-testid="grace2-case-row"]', { timeout: 5_000 });
      await page.waitForTimeout(300);
      await shot(page, `desk_root_${theme}.png`);
      await ctx.close();
    }
    {
      const { ctx, page } = await newPage(browser, { width: 1440, height: 900, theme });
      await injectCaseOpen(page);
      await page.waitForSelector('[data-testid="grace2-case-view"]', { timeout: 5_000 });
      await page.waitForSelector('[data-testid="grace2-layer-panel"]', { timeout: 5_000 });
      // NO running pipeline injection here: a ticking elapsed timer would
      // make before/after byte comparison impossible. Complete cards only.
      await page.waitForTimeout(1_500); // legend mounts, tiles settle
      await shot(page, `desk_case_${theme}.png`);
      await ctx.close();
    }
  }

  // --- Mobile evidence (390x844) ------------------------------------------ //

  // 1. Collapsed sheet at root over the map.
  {
    const { ctx, page } = await newPage(browser, { width: 390, height: 844, mobile: true });
    await page.waitForSelector(
      '[data-testid="grace2-chat"][data-sheet-state="collapsed"]',
      { timeout: 15_000 },
    );
    await page.waitForTimeout(500);
    await shot(page, "mobile_sheet_collapsed.png");
    await ctx.close();
  }

  // 2. Expanded sheet inside a Case: replayed bubbles + complete tool card +
  //    injected RUNNING tool card.
  {
    const { ctx, page } = await newPage(browser, { width: 390, height: 844, mobile: true });
    await injectCaseOpen(page);
    await page.click('[data-testid="grace2-chat-sheet-toggle"]');
    await page.waitForSelector(
      '[data-testid="grace2-chat"][data-sheet-state="expanded"]',
      { timeout: 5_000 },
    );
    await page.evaluate((snap) => {
      window.__grace2InjectPipelineState(snap);
    }, RUNNING_SNAP);
    await page.waitForTimeout(1_300); // timer ticks
    await shot(page, "mobile_sheet_expanded.png");
    await ctx.close();
  }

  // 3. Drawer open at root: Cases list floating over the map.
  {
    const { ctx, page } = await newPage(browser, { width: 390, height: 844, mobile: true });
    await injectCases(page);
    await page.click('[data-testid="grace2-mobile-drawer-button"]');
    await page.waitForSelector('[data-testid="grace2-mobile-drawer"]', { timeout: 5_000 });
    await page.waitForTimeout(400);
    await shot(page, "mobile_drawer_root.png");
    await ctx.close();
  }

  // 4. Drawer open inside a Case: breadcrumb (single Cases back affordance)
  //    + LayerPanel + footer pills floating over the map.
  {
    const { ctx, page } = await newPage(browser, { width: 390, height: 844, mobile: true });
    await injectCaseOpen(page);
    await page.click('[data-testid="grace2-mobile-drawer-button"]');
    await page.waitForSelector('[data-testid="grace2-mobile-drawer"]', { timeout: 5_000 });
    await page.waitForSelector('[data-testid="grace2-case-view"]', { timeout: 5_000 });
    await page.waitForTimeout(400);
    await shot(page, "mobile_drawer_case.png");
    await ctx.close();
  }

  await browser.close();
  console.log(`OK — ${PREFIX} screenshots written to`, OUT);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
