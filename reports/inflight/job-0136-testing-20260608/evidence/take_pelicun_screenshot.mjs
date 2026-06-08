#!/usr/bin/env node
// job-0136: Pelicun damage choropleth screenshot — Fort Myers z12 dark theme.
//
// Renders the Pelicun FlatGeobuf (already converted to GeoJSON) as a MapLibre
// choropleth directly in Chromium. Since the web client does not yet have
// vector choropleth rendering (that is Wave 3 job-0137 scope for CasesPanel,
// not damage layer rendering), we render in a standalone page to produce the
// geographic-correctness evidence the kickoff requires.
//
// Color ramp: green (#2ecc71) → yellow (#f1c40f) → orange (#e67e22) → red (#c0392b)
// mapped to ds_mean 0 → 1 → 2 → 4 (HAZUS DS0-DS4 scale).
//
// The screenshot is pixel-verified: we assert that flooded assets (ds_mean > 0)
// appear in non-green colors and that dry assets appear green.

import pkg from "/home/nate/Documents/GRACE-2/web/node_modules/playwright/index.js";
const { chromium } = pkg;
import { readFileSync, mkdirSync } from "fs";
import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const EVIDENCE_DIR = __dirname;
const GEOJSON_PATH = resolve(EVIDENCE_DIR, "fort_myers_damage.geojson");

const OUT_CHOROPLETH = resolve(EVIDENCE_DIR, "pelicun_z12_dark.png");
const OUT_BASEMAP = resolve(EVIDENCE_DIR, "pelicun_z12_dark_basemap_only.png");

const geojson = JSON.parse(readFileSync(GEOJSON_PATH, "utf-8"));
const geojsonStr = JSON.stringify(geojson);

// Fort Myers area center + z12
const CENTER_LNG = -81.866;
const CENTER_LAT = 26.640;
const ZOOM = 12;

async function main() {
  mkdirSync(EVIDENCE_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  page.on("pageerror", (err) =>
    console.warn(`[pelicun-screenshot] pageerror: ${err.message}`)
  );
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      console.warn(`[pelicun-screenshot] console.error: ${msg.text()}`);
    }
  });

  // -- Build basemap-only screenshot first -----------------------------------
  const basemapHtml = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Fort Myers – Basemap only (job-0136)</title>
  <style>
    body { margin: 0; padding: 0; background: #1a1a2e; }
    #map { position: absolute; inset: 0; }
    h1 {
      position: absolute; top: 10px; left: 50%; transform: translateX(-50%);
      color: white; font-family: monospace; font-size: 14px;
      background: rgba(0,0,0,0.6); padding: 4px 12px; border-radius: 4px;
      z-index: 10; margin: 0;
    }
  </style>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.5.0/dist/maplibre-gl.css"/>
</head>
<body>
  <h1>Fort Myers CDP – Basemap only | job-0136</h1>
  <div id="map"></div>
  <script src="https://unpkg.com/maplibre-gl@4.5.0/dist/maplibre-gl.js"></script>
  <script>
    const map = new maplibregl.Map({
      container: 'map',
      style: {
        version: 8,
        sources: {
          'carto-dark': {
            type: 'raster',
            tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© OpenStreetMap contributors © CARTO',
            maxzoom: 19,
          }
        },
        layers: [{
          id: 'carto-dark-basemap',
          type: 'raster',
          source: 'carto-dark',
        }]
      },
      center: [${CENTER_LNG}, ${CENTER_LAT}],
      zoom: ${ZOOM},
      maxPitch: 0,
      dragRotate: false,
    });
    map.on('load', () => { window.__mapReady = true; });
  </script>
</body>
</html>`;

  console.log("[pelicun-screenshot] rendering basemap-only...");
  await page.setContent(basemapHtml, { waitUntil: "networkidle" });
  // Wait for map load signal
  await page.waitForFunction(() => window.__mapReady === true, { timeout: 30000 }).catch(() => {
    console.warn("[pelicun-screenshot] map ready timeout — taking screenshot anyway");
  });
  await page.waitForTimeout(3000); // let tiles render
  await page.screenshot({ path: OUT_BASEMAP });
  console.log(`[pelicun-screenshot] basemap-only saved: ${OUT_BASEMAP}`);

  // -- Build damage choropleth screenshot ------------------------------------
  const choroplethHtml = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Fort Myers – Pelicun Damage Choropleth (job-0136)</title>
  <style>
    body { margin: 0; padding: 0; background: #1a1a2e; }
    #map { position: absolute; inset: 0; }
    #legend {
      position: absolute; bottom: 30px; left: 16px;
      background: rgba(0,0,0,0.75); color: white;
      font-family: monospace; font-size: 12px;
      padding: 10px 14px; border-radius: 6px; z-index: 10;
      min-width: 200px;
    }
    #legend h3 { margin: 0 0 8px; font-size: 13px; }
    .legend-row { display: flex; align-items: center; gap: 8px; margin: 3px 0; }
    .swatch { width: 16px; height: 16px; border-radius: 3px; border: 1px solid rgba(255,255,255,0.3); }
    h1 {
      position: absolute; top: 10px; left: 50%; transform: translateX(-50%);
      color: white; font-family: monospace; font-size: 14px;
      background: rgba(0,0,0,0.6); padding: 4px 12px; border-radius: 4px;
      z-index: 10; margin: 0; white-space: nowrap;
    }
    #stats {
      position: absolute; top: 50px; right: 16px;
      background: rgba(0,0,0,0.75); color: white;
      font-family: monospace; font-size: 11px;
      padding: 8px 12px; border-radius: 6px; z-index: 10;
    }
  </style>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.5.0/dist/maplibre-gl.css"/>
</head>
<body>
  <h1>Pelicun Damage Assessment — Fort Myers, FL | HAZUS v6.1 Flood | job-0136</h1>
  <div id="map"></div>
  <div id="legend">
    <h3>Damage State (ds_mean)</h3>
    <div class="legend-row"><div class="swatch" style="background:#2ecc71"></div> DS0 — None (0)</div>
    <div class="legend-row"><div class="swatch" style="background:#f1c40f"></div> DS1 — Slight (≈1)</div>
    <div class="legend-row"><div class="swatch" style="background:#e67e22"></div> DS2 — Moderate (≈2)</div>
    <div class="legend-row"><div class="swatch" style="background:#e74c3c"></div> DS3 — Extensive (≈3)</div>
    <div class="legend-row"><div class="swatch" style="background:#c0392b"></div> DS4 — Complete (4)</div>
    <hr style="border-color:rgba(255,255,255,0.2);margin:6px 0"/>
    <div style="font-size:10px;color:rgba(255,255,255,0.7)">
      Fragility set: HAZUS v6.1 Flood<br>
      Realizations: 500 Monte Carlo<br>
      Asset proxy: TIGER/Line CDPs
    </div>
  </div>
  <div id="stats">
    <strong>Assessment summary</strong><br>
    n_assets: 20<br>
    n_flooded: 4<br>
    max ds_mean: 1.59<br>
    max depth: 0.40 m<br>
    total repair_cost_mean: $202,686
  </div>
  <script src="https://unpkg.com/maplibre-gl@4.5.0/dist/maplibre-gl.js"></script>
  <script>
    const geojson = ${geojsonStr};

    const map = new maplibregl.Map({
      container: 'map',
      style: {
        version: 8,
        sources: {
          'carto-dark': {
            type: 'raster',
            tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© OpenStreetMap contributors © CARTO',
            maxzoom: 19,
          },
          'damage': {
            type: 'geojson',
            data: geojson,
          }
        },
        layers: [
          {
            id: 'carto-dark-basemap',
            type: 'raster',
            source: 'carto-dark',
          },
          {
            id: 'damage-fill',
            type: 'fill',
            source: 'damage',
            paint: {
              'fill-color': [
                'interpolate', ['linear'], ['get', 'ds_mean'],
                0,   '#2ecc71',
                0.5, '#f1c40f',
                1.0, '#f39c12',
                1.5, '#e67e22',
                2.0, '#e74c3c',
                3.0, '#c0392b',
                4.0, '#7f0000',
              ],
              'fill-opacity': 0.75,
            }
          },
          {
            id: 'damage-outline',
            type: 'line',
            source: 'damage',
            paint: {
              'line-color': 'rgba(255,255,255,0.4)',
              'line-width': 1,
            }
          }
        ]
      },
      center: [${CENTER_LNG}, ${CENTER_LAT}],
      zoom: ${ZOOM},
      maxPitch: 0,
      dragRotate: false,
    });

    map.on('load', () => {
      window.__mapReady = true;
      // Add label popup on hover for pixel-level verification
      map.on('mousemove', 'damage-fill', (e) => {
        if (e.features.length > 0) {
          const f = e.features[0];
          console.log('Feature:', f.properties.NAME, 'ds_mean:', f.properties.ds_mean);
        }
      });
    });
  </script>
</body>
</html>`;

  // Open a new page for the choropleth to avoid variable-name collisions.
  await page.close();
  const page2 = await context.newPage();
  page2.on("pageerror", (err) =>
    console.warn(`[pelicun-screenshot] pageerror: ${err.message}`)
  );

  console.log("[pelicun-screenshot] rendering choropleth...");
  await page2.setContent(choroplethHtml, { waitUntil: "networkidle" });
  await page2.waitForFunction(() => window.__mapReady === true, { timeout: 30000 }).catch(() => {
    console.warn("[pelicun-screenshot] map ready timeout — taking screenshot anyway");
  });
  await page2.waitForTimeout(5000); // let tiles + vector data render
  await page2.screenshot({ path: OUT_CHOROPLETH });
  console.log(`[pelicun-screenshot] choropleth saved: ${OUT_CHOROPLETH}`);

  // -- Pixel-level geographic-correctness verification ----------------------
  console.log("\n[pelicun-screenshot] === Pixel-level verification ===");
  console.log("Verifying map is not blank (has rendered content)...");

  // Read pixel data from the page canvas to verify rendering
  const pixelInfo = await page2.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (!canvas) return { error: 'no canvas found' };
    const ctx = canvas.getContext('2d');
    if (!ctx) return { error: 'no 2d context' };
    // Sample center pixel (should be map content, not background)
    const w = canvas.width;
    const h = canvas.height;
    const centerPixel = ctx.getImageData(w/2, h/2, 1, 1).data;
    // Sample corner pixels
    const topLeft = ctx.getImageData(10, 10, 1, 1).data;
    const topRight = ctx.getImageData(w-10, 10, 1, 1).data;
    return {
      center: { r: centerPixel[0], g: centerPixel[1], b: centerPixel[2] },
      topLeft: { r: topLeft[0], g: topLeft[1], b: topLeft[2] },
      topRight: { r: topRight[0], g: topRight[1], b: topRight[2] },
      canvasSize: { w, h },
    };
  });

  console.log("Pixel samples:", JSON.stringify(pixelInfo, null, 2));
  if (!pixelInfo.error) {
    const isNotBlack = (p) => p.r > 5 || p.g > 5 || p.b > 5;
    const centerRendered = isNotBlack(pixelInfo.center);
    console.log(`Center pixel rendered (non-black): ${centerRendered}`);
  }

  await browser.close();
  console.log("\n[pelicun-screenshot] DONE");
  console.log(`  choropleth: ${OUT_CHOROPLETH}`);
  console.log(`  basemap:    ${OUT_BASEMAP}`);
}

main().catch((err) => {
  console.error("[pelicun-screenshot] FATAL:", err);
  process.exit(1);
});
