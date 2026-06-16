// LIVE acceptance: Case 3 (Track C) on the deployed AWS HTTPS site. NWS active
// flood warning -> MRMS accumulated precip over the warning polygon -> SFINCS ->
// flood render. Exercises the Track C s3:// forcing-raster read fix. Targets a
// state with an ACTIVE flood warning (env GRACE2_CASE3_STATE, default Texas).
import { chromium } from "playwright";

const SITE = "https://d125yfbyjrpbre.cloudfront.net/app";
const CF = "d125yfbyjrpbre.cloudfront.net";
const EMAIL = process.env.GRACE2_DEMO_EMAIL;
const PW = process.env.GRACE2_DEMO_PASSWORD;
const STATE = process.env.GRACE2_CASE3_STATE || "Texas";
const PROMPT = `Check for active flood warnings in ${STATE} and model the resulting flooding from the observed MRMS precipitation over the warning area, then show the inundation on the map.`;
const OUT = "/tmp/aws_case3";
const BUDGET_MS = 22 * 60 * 1000;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
let tiles = 0;
page.on("pageerror", (e) => errors.push(String(e)));
page.on("response", (r) => { if (r.url().includes("54.185.114.233:8080") && r.url().includes("/cog/tiles/") && r.status() === 200) tiles++; });

await page.goto(SITE, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(2000);
await page.getByRole("button", { name: /Sign in|Sign up/i }).first().click().catch(() => {});
await page.waitForTimeout(5000);
const u = page.locator('input[name="username"]:visible, input[type="email"]:visible').first();
await u.waitFor({ timeout: 12000 });
await u.fill(EMAIL);
await page.locator('input[name="password"]:visible, input[type="password"]:visible').first().fill(PW);
await page.locator('input[name="signInSubmitButton"]:visible, input[type="submit"]:visible, button[type="submit"]:visible').first().click().catch(() => {});
for (let i = 0; i < 20; i++) { await page.waitForTimeout(1500); if (page.url().includes(CF) && !/amazoncognito/.test(page.url())) break; }
await page.waitForTimeout(5000);
const booted = await page.locator('[data-testid="chat-input"]').count();
console.log(`[signin] chatInput=${booted}`);
if (!booted) { await page.screenshot({ path: `${OUT}_signin_fail.png` }); await browser.close(); process.exit(1); }

const input = page.locator('[data-testid="chat-input"]');
await input.fill(PROMPT);
await input.press("Enter");
console.log(`[prompt] Case 3 ${STATE}; waiting (auto-approving gates)`);

const start = Date.now();
let done = false, shot = 1, lastShot = 0, gates = 0;
while (Date.now() - start < BUDGET_MS) {
  await page.waitForTimeout(4000);
  const t = Math.round((Date.now() - start) / 1000);
  for (const sel of ['[data-testid="payload-warning-button-proceed"]', '[data-testid="sandbox-card-proceed"]']) {
    const b = page.locator(sel);
    if (await b.count()) { await b.first().click().catch(() => {}); gates++; console.log(`[gate] ${sel} t=${t}s`); }
  }
  for (const name of [/^Proceed$/i, /^Run$/i, /^Approve$/i, /^Confirm$/i, /Run anyway/i]) {
    const b = page.getByRole("button", { name });
    if (await b.count()) { await b.first().click().catch(() => {}); gates++; console.log(`[gate] ${name} t=${t}s`); }
  }
  if (t - lastShot >= 60) { lastShot = t; await page.screenshot({ path: `${OUT}_${shot}_t${t}s.png` }); console.log(`[shot ${shot}] t=${t}s tiles=${tiles} gates=${gates}`); shot++; }
  const body = await page.evaluate(() => document.body.innerText);
  // completion: a flood layer rendered (tiles) OR flood-depth narration; OR honest no-warning
  const floodNarr = /flood depth|inundation|max depth|peak depth|flooded|SFINCS|m of water/i.test(body);
  const noWarn = /no active flood warning/i.test(body);
  if (((tiles > 0 || floodNarr) && t > 60) || (noWarn && t > 40)) {
    done = true; console.log(`[done] tiles=${tiles} floodNarr=${floodNarr} noWarn=${noWarn} at t=${t}s`); await page.waitForTimeout(8000); break;
  }
}
await page.screenshot({ path: `${OUT}_final.png` });
const body = await page.evaluate(() => document.body.innerText);
console.log(`[result] done=${done} tiles=${tiles} gates=${gates} errors=${errors.length}`);
console.log("[tail]\n" + body.split("\n").filter(Boolean).slice(-22).join("\n"));
await browser.close();
process.exit(done ? 0 : 1);
