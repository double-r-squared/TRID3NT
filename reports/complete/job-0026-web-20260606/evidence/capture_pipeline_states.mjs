#!/usr/bin/env node
// job-0026 evidence capture — drives the PipelineStrip through its five
// rendered states + an empty/idle baseline + a cross-envelope predicate
// check, captures PNGs + a WS-frame transcript on cancel-click.
//
// Usage:
//   node capture_pipeline_states.mjs --browser=chromium
//   node capture_pipeline_states.mjs --browser=firefox
//
// Outputs into the same directory: <state>-<browser>.png

import { mkdir } from "node:fs/promises";
import { writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
// __dirname = .../reports/inflight/job-0026-web-20260606/evidence
// repo root  = .../  (5 ".." segments up)
const PLAYWRIGHT_ENTRY = resolve(
  __dirname,
  "..",
  "..",
  "..",
  "..",
  "web",
  "node_modules",
  "@playwright",
  "test",
  "index.mjs",
);
if (!existsSync(PLAYWRIGHT_ENTRY)) {
  console.error(`@playwright/test not at ${PLAYWRIGHT_ENTRY}`);
  process.exit(2);
}
const { chromium, firefox } = await import(PLAYWRIGHT_ENTRY);

const browserName = (process.argv.find((a) => a.startsWith("--browser=")) || "--browser=chromium").split("=")[1];
const launcher = browserName === "firefox" ? firefox : chromium;

await mkdir(__dirname, { recursive: true });

// 5 pipeline_state fixtures spanning all 5 state colors, plus a cancel test.
const FIXTURES = {
  // initial = no envelope sent; expect "idle" empty rendering
  "initial": null,

  // running = single in-progress step with progress + earlier completed step
  "running": {
    pipeline_id: "01HZZRUNNING0000000000000",
    steps: [
      {
        step_id: "step-fetch",
        name: "fetch_event_metadata",
        tool_name: "fetch_event_metadata",
        state: "complete",
        progress_percent: 100,
        started_at: "2026-06-06T18:00:00Z",
        completed_at: "2026-06-06T18:00:02Z",
      },
      {
        step_id: "step-aggregate",
        name: "aggregate_claims",
        tool_name: "aggregate_claims_across_sources",
        state: "running",
        progress_percent: 47,
        started_at: "2026-06-06T18:00:02Z",
      },
      {
        step_id: "step-render",
        name: "render_layers",
        tool_name: "update_project_layers",
        state: "pending",
      },
    ],
  },

  // complete = all steps complete
  "complete": {
    pipeline_id: "01HZZCOMPLETE000000000000",
    steps: [
      {
        step_id: "step-fetch",
        name: "fetch_event_metadata",
        tool_name: "fetch_event_metadata",
        state: "complete",
        progress_percent: 100,
      },
      {
        step_id: "step-aggregate",
        name: "aggregate_claims",
        tool_name: "aggregate_claims_across_sources",
        state: "complete",
        progress_percent: 100,
      },
      {
        step_id: "step-render",
        name: "render_layers",
        tool_name: "update_project_layers",
        state: "complete",
        progress_percent: 100,
      },
    ],
  },

  // failed = final step failed with error_code + error_message
  "failed": {
    pipeline_id: "01HZZFAILED00000000000000",
    steps: [
      {
        step_id: "step-fetch",
        name: "fetch_event_metadata",
        tool_name: "fetch_event_metadata",
        state: "complete",
        progress_percent: 100,
      },
      {
        step_id: "step-solver",
        name: "run_sfincs_solver",
        tool_name: "run_solver",
        state: "failed",
        error_code: "SOLVER_FAILED",
        error_message: "SFINCS exit code 1: missing forcing file dem.nc",
      },
    ],
  },

  // cancelled = pipeline cancelled mid-flight
  "cancelled": {
    pipeline_id: "01HZZCANCELLED00000000000",
    steps: [
      {
        step_id: "step-fetch",
        name: "fetch_event_metadata",
        tool_name: "fetch_event_metadata",
        state: "complete",
        progress_percent: 100,
      },
      {
        step_id: "step-aggregate",
        name: "aggregate_claims",
        tool_name: "aggregate_claims_across_sources",
        state: "cancelled",
      },
      {
        step_id: "step-render",
        name: "render_layers",
        tool_name: "update_project_layers",
        state: "cancelled",
      },
    ],
  },
};

// session-state with current_pipeline non-null — used to demonstrate
// predicate (b) (cancel button visible even without a running step in
// pipeline-state).
const SESSION_STATE_WITH_CURRENT = {
  chat_history: [],
  loaded_layers: [],
  pipeline_history: [],
  current_pipeline: {
    pipeline_id: "01HZZSESSION00000000000000",
    started_at: "2026-06-06T18:00:00Z",
    steps: [
      {
        step_id: "step-1",
        name: "warming_up",
        tool_name: "fetch_event_metadata",
        state: "pending",
      },
    ],
  },
  map_view: null,
};

const SESSION_STATE_NULL_CURRENT = {
  chat_history: [],
  loaded_layers: [],
  pipeline_history: [],
  current_pipeline: null,
  map_view: null,
};

const wsFrames = [];

async function capture(page, state, name, opts = {}) {
  if (state !== null) {
    await page.evaluate((s) => {
      window.__grace2InjectPipelineState?.(s);
    }, state);
  }
  if (opts.sessionState) {
    await page.evaluate((s) => {
      window.__grace2InjectSessionState?.(s);
    }, opts.sessionState);
  }
  if (opts.resetPipeline) {
    // Inject an empty replacement to demonstrate replace-not-reconcile.
    await page.evaluate((s) => {
      window.__grace2InjectPipelineState?.(s);
    }, opts.resetPipeline);
  }
  await page.waitForTimeout(500);
  const out = join(__dirname, `${name}-${browserName}.png`);
  await page.screenshot({ path: out });
  console.log(`wrote ${out}`);
}

const browser = await launcher.launch({ headless: true });
try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.on("console", (msg) => {
    if (msg.type() === "error" || msg.type() === "warning") {
      console.warn(`[console.${msg.type()}] ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => console.warn(`[pageerror] ${err.message}`));

  // Playwright native WebSocket frame interception — far more reliable
  // than a JS shim because it sits on the CDP/protocol layer rather than
  // in-page object replacement.
  page.on("websocket", (ws) => {
    const url = ws.url();
    ws.on("framesent", (data) => {
      const payload = typeof data.payload === "string" ? data.payload : "(binary)";
      wsFrames.push({ direction: "out", url, payload });
    });
    ws.on("framereceived", (data) => {
      const payload = typeof data.payload === "string" ? data.payload : "(binary)";
      wsFrames.push({ direction: "in", url, payload });
    });
  });

  const resp = await page.goto("http://localhost:5173/", { waitUntil: "load", timeout: 30000 });
  if (!resp || !resp.ok()) console.warn(`navigation ${resp ? resp.status() : "no-resp"}`);
  await page.waitForTimeout(1500);

  // 1. initial — no pipeline-state yet — predicate should be (a)=false, (b)=false → no cancel button
  await capture(page, null, "initial");

  // 2. running — predicate (a)=true → cancel button visible
  await capture(page, FIXTURES["running"], "running");

  // 3. complete — predicate (a)=false, (b)=false → cancel hidden
  // First reset session-state to clear any current_pipeline from earlier
  await page.evaluate((s) => { window.__grace2InjectSessionState?.(s); }, SESSION_STATE_NULL_CURRENT);
  await capture(page, FIXTURES["complete"], "complete");

  // 4. failed — same predicate; cancel hidden; failed step renders error_code + message
  await capture(page, FIXTURES["failed"], "failed");

  // 5. cancelled — predicate (a)=false; cancel hidden; cancelled chips render distinct yellow
  await capture(page, FIXTURES["cancelled"], "cancelled");

  // 6. predicate (b) only — pipeline-state with no running step BUT session-state.current_pipeline non-null
  await page.evaluate((s) => { window.__grace2InjectSessionState?.(s); }, SESSION_STATE_WITH_CURRENT);
  await capture(page, FIXTURES["complete"], "predicate-b-only");

  // 7. Cancel click — emits a cancel envelope (predicate b is satisfied from prior step)
  // Should appear as out-bound WS frame with type=cancel
  await page.locator('[data-testid="pipeline-cancel"]').click();
  await page.waitForTimeout(500);

  // 8. Replace-not-reconcile demonstration: inject a snapshot with a DIFFERENT
  // disjoint set of steps and confirm the prior steps are gone.
  await page.evaluate((s) => { window.__grace2InjectSessionState?.(s); }, SESSION_STATE_NULL_CURRENT);
  await capture(page, {
    pipeline_id: "01HZZREPLACE000000000000",
    steps: [
      {
        step_id: "step-new-1",
        name: "totally_new_step",
        tool_name: "fetch_event_metadata",
        state: "running",
        progress_percent: 10,
      },
    ],
  }, "replace-not-reconcile");

  await ctx.close();
} finally {
  await browser.close();
}

const transcriptPath = join(__dirname, `ws-frames-${browserName}.json`);
writeFileSync(transcriptPath, JSON.stringify(wsFrames, null, 2));
console.log(`captured ${wsFrames.length} ws frames → ${transcriptPath}`);
