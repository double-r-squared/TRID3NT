// LIVE-VERIFY proof (2): real MapLibre session renders the basemap THROUGH the
// QGIS proxy. The web client is built with VITE_QGIS_PROXY_BASE pointing at the
// throwaway proxy (:8910); applyQgisProxy() rewrites WMS tile URLs to it. We
// capture every network request and confirm tiles hit /qgis-proxy with 200s,
// then screenshot the rendered map.
import pw from "/home/nate/Documents/GRACE-2/web/node_modules/playwright/index.js";
const { chromium } = pw;
import { writeFileSync } from "node:fs";

const URL = process.argv[2] || "http://127.0.0.1:5194/app";
const OUT = process.argv[3] || "/tmp/maplibre_proxy";

const proxyRequests = [];   // requests to /qgis-proxy
const proxyResponses = [];  // their responses

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
await ctx.addInitScript(() => {
  try { window.localStorage.setItem("grace2_anonymous_accepted", "1"); } catch {}
});
const page = await ctx.newPage();

page.on("request", (req) => {
  const u = req.url();
  if (u.includes("/qgis-proxy")) proxyRequests.push(u);
});
page.on("requestfinished", async (req) => {
  const u = req.url();
  if (u.includes("/qgis-proxy")) {
    try {
      const resp = await req.response();
      if (resp) proxyResponses.push({
        status: resp.status(),
        contentType: (resp.headers()["content-type"] || ""),
        acao: resp.headers()["access-control-allow-origin"] || "",
      });
    } catch {}
  }
});

await page.goto(URL, { waitUntil: "networkidle" }).catch(() => {});
// Click through the AuthGate (disabled mode) so the workbench + MapLibre mount.
try {
  await page.locator('[data-testid="grace2-auth-gate-anonymous"]').click({ timeout: 8000 });
} catch (e) { console.error("authgate click:", e.message); }
// Wait for the app shell to mount (map container).
try {
  await page.locator('[data-testid="grace2-app-shell"]').waitFor({ timeout: 10000 });
  console.error("app-shell mounted");
} catch (e) { console.error("app-shell wait:", e.message); }
// Let MapLibre request basemap tiles + decode + paint. Longer settle so the
// raster tiles finish loading rather than being cancelled mid-flight.
await page.waitForTimeout(15000);
await page.screenshot({ path: `${OUT}_render.png`, fullPage: false }).catch(() => {});

const result = {
  url: URL,
  proxy_request_count: proxyRequests.length,
  proxy_response_summary: proxyResponses.slice(0, 10),
  proxy_200_png_count: proxyResponses.filter(
    (r) => r.status === 200 && r.contentType.includes("image/png"),
  ).length,
  sample_proxy_request: proxyRequests[0] ?? null,
};
writeFileSync(`${OUT}_network.json`, JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
await browser.close();
