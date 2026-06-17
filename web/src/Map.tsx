// GRACE-2 web — MapLibre GL JS CONUS basemap + WMS overlay wiring.
//
// M3 pivot (job-0025):
//   The default basemap is now sourced from the deployed QGIS Server WMS at
//   /ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=basemap-osm-conus (see
//   job-0024 audit — `.qgs` mounted at `/mnt/qgs/` via Cloud Run gen2 native
//   GCS volume mount; image digest @sha256:a703476049…). This satisfies
//   FR-WC-2 (Tier B / QGIS Server rendering path) and Invariant 4 (rendering
//   through QGIS Server) for the basemap-layer slice. Tier separation
//   (Invariant 5) is preserved: zero `gs://` URLs in client code; the client
//   talks to the QGIS Server endpoint only.
//
//   The OSM-direct raster source from M1 is KEPT in the style as an inactive
//   fallback layer (`layout.visibility = 'none'`). This is the FR-DT-1
//   swappability proof — flipping the visibility in the style spec swaps
//   the basemap source without touching the agent. No runtime feature-flag
//   plumbing (per "No legacy support pre-MVP").
//
// FR-WC-1, FR-WC-3, FR-DT-3, Decision I (preserved verbatim from M1):
//   - Initial view fits CONUS (lng -95.5, lat 37, zoom 4).
//   - Camera locked 2D: maxPitch:0, dragRotate disabled, no touch rotate.
//   - Pan + zoom enabled. No layer panel here (LayerPanel.tsx owns that).
//
// job-0068 additions:
//   - Subscribes to session-state.loaded_layers and wires WMS raster sources
//     via MapLibre addSource/addLayer (Invariant 4 — QGIS Server renders;
//     client only registers URLs). Replace-not-reconcile per A.7: diffs
//     against a useRef<Set<string>> of added source IDs.
//   - Subscribes to map-command and handles zoom-to via map.fitBounds.
//
// The client renders, it never computes — every number on the map is a
// MapLibre-internal coordinate (Invariant 1 preserved trivially).

import { useEffect, useRef, useState } from "react";
import maplibregl, { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { MapCommandPayload, SessionStatePayload, ProjectLayerSummary, RegionCandidate } from "./contracts";
import type { FeatureCollection, Feature, Polygon, Geometry } from "geojson";
import { publicTileBase } from "./lib/public_base";
import { regionChoiceBus, type RegionChoiceBusState } from "./lib/region_choice_bus";
import { LayerLegend } from "./components/LayerLegend";
import { FeaturePopup, type FeaturePopupData, type FeatureAttribute } from "./components/FeaturePopup";
import { useIsMobile } from "./hooks/useIsMobile";
import {
  fetchVectorAsGeoJson,
  vectorResultFromInlineGeoJson,
  resolveVectorColor,
  isPelicunDamageLayer,
  buildDsMeanExpression,
  POLYGON_FILL_OPACITY,
  POLYGON_STROKE_WIDTH,
  CLUSTER_THRESHOLD,
  CLUSTER_RADIUS,
  type VectorGeomKind,
} from "./lib/vector_rendering";

/** UI theme — see App.tsx for toggle implementation (job-0076). */
export type MapTheme = "light" | "dark";

/**
 * CartoDB DarkMatter raster tiles (CC-BY, no API key). Used as the dark-theme
 * basemap. Raster (not vector) is chosen for two reasons:
 *   1. The light-theme basemap is also raster (QGIS Server WMS), so swapping
 *      raster-for-raster preserves the layer/source type and avoids re-tuning
 *      paint props for the flood overlay.
 *   2. The vector style.json brings in glyphs/sprites + multiple sub-sources
 *      that complicate the swap path; raster is one-source one-layer.
 * Attribution per CartoDB ToS: "© OpenStreetMap contributors © CARTO".
 */
const CARTO_DARK_TILE_TEMPLATE = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png";
const CARTO_DARK_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors © <a href="https://carto.com/attributions" target="_blank" rel="noopener noreferrer">CARTO</a>';

// LIGHT-theme basemap — CartoDB Positron raster (CC-BY, no API key, CDN).
// ux-batch-1 GCP-DECOUPLE FIX (2026-06-16): the light basemap previously
// pointed at the GCP Cloud Run QGIS Server (DEFAULT_WMS_URL below), a lingering
// GCP dependency missed in the AWS migration — and that server is private
// (invoker-only) so the prod site got 403s and the map never settled, which
// stalled every deferred layer/extent draw (the "layers in panel but not on
// map / waits to go light->dark" incident). Positron mirrors the dark CartoDB
// basemap (raster, one-source-one-layer), needs no GCP and no QGIS Server, and
// keeps both themes working until QGIS Server is re-hosted on AWS (sprint-16).
const CARTO_LIGHT_TILE_TEMPLATE = "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png";
const CARTO_LIGHT_ATTRIBUTION = CARTO_DARK_ATTRIBUTION;

// QGIS Server WMS endpoint. Overridable via VITE_GRACE2_WMS_URL at build/dev
// start. Default = deployed M2 substrate (job-0018 + job-0024).
//
// NOTE: the MAP= query string IS part of the WMS endpoint contract here —
// QGIS Server keys projects by the filesystem-mounted `.qgs` path. Per the
// FR-QS-2 amendment surfaced from job-0024, `.qgs` reaches QGIS Server via
// the /mnt/qgs/ Cloud Run gen2 native GCS volume mount; layer-data refs
// INSIDE the `.qgs` still use /vsigs/.
const DEFAULT_WMS_URL =
  "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs";

// job-0255 (sprint-13.5): env-gated QGIS proxy base. When VITE_QGIS_PROXY_BASE
// is set (prod), every QGIS Server WMS URL is rewritten so its
// scheme+host+path is replaced by the agent's /qgis-proxy endpoint and the
// original WMS query string is preserved. The agent proxy (which holds the
// only invoker grant on the now-private QGIS Server) forwards + streams the
// tile, stripping user credentials. ABSENT (dev/today) → returns the URL
// byte-identical, so behavior is unchanged. Example:
//   VITE_QGIS_PROXY_BASE = "https://agent.example/qgis-proxy"
//   https://qgis.run.app/ogc/wms?MAP=x&LAYERS=y
//     → https://agent.example/qgis-proxy?MAP=x&LAYERS=y
const QGIS_PROXY_BASE: string | undefined =
  (import.meta.env.VITE_QGIS_PROXY_BASE as string | undefined) || undefined;

export function applyQgisProxy(wmsUrl: string): string {
  if (!QGIS_PROXY_BASE) return wmsUrl; // dev/today: byte-identical passthrough.
  const qIdx = wmsUrl.indexOf("?");
  const query = qIdx >= 0 ? wmsUrl.slice(qIdx + 1) : "";
  const base = QGIS_PROXY_BASE.replace(/[?&]+$/, "");
  return query ? `${base}?${query}` : base;
}

const WMS_BASE_URL: string = applyQgisProxy(
  (import.meta.env.VITE_GRACE2_WMS_URL as string | undefined) ?? DEFAULT_WMS_URL,
);

// MapLibre injects {bbox-epsg-3857} into the tile URL with the tile's
// bounding box in EPSG:3857 (the default Web Mercator projection). QGIS
// Server returns a 256×256 PNG per tile request.
const WMS_TILE_TEMPLATE = `${WMS_BASE_URL}&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=basemap-osm-conus&CRS=EPSG:3857&FORMAT=image/png&TRANSPARENT=true&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256&STYLES=`;

// OSM Tier A fallback. Kept committed to demonstrate FR-DT-1 swappability;
// the visibility flag is 'none' so it does not render at runtime.
const OSM_TILE_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors';
const QGIS_WMS_ATTRIBUTION =
  'Basemap via GRACE-2 QGIS Server — © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors';

const CONUS_VIEW = {
  center: [-95.5, 37.0] as [number, number],
  zoom: 4,
};

const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    // LIGHT basemap. ux-batch-1 GCP-decouple (2026-06-16): was the GCP QGIS
    // Server WMS (now private/unreachable from prod → dead map). Swapped to
    // CartoDB Positron (CDN, no GCP, no QGIS Server). Source id kept as
    // "qgis-wms" so the theme-swap / beforeId logic below is unchanged; it now
    // serves CartoDB Positron tiles. (Re-point at QGIS Server once it is on AWS
    // — sprint-16 — via VITE_GRACE2_WMS_URL.)
    "qgis-wms": {
      type: "raster",
      tiles: [
        (import.meta.env.VITE_GRACE2_WMS_URL as string | undefined)
          ? WMS_TILE_TEMPLATE
          : CARTO_LIGHT_TILE_TEMPLATE,
      ],
      tileSize: 256,
      attribution: (import.meta.env.VITE_GRACE2_WMS_URL as string | undefined)
        ? QGIS_WMS_ATTRIBUTION
        : CARTO_LIGHT_ATTRIBUTION,
      maxzoom: 19,
    },
    // Inactive fallback: OSM direct. FR-DT-1 Tier A swappability proof —
    // present in the style spec but `visibility: 'none'`. No runtime swap
    // affordance (no legacy support pre-MVP).
    "osm-fallback": {
      type: "raster",
      tiles: [OSM_TILE_TEMPLATE],
      tileSize: 256,
      attribution: OSM_ATTRIBUTION,
      maxzoom: 19,
    },
  },
  layers: [
    {
      id: "qgis-basemap",
      type: "raster",
      source: "qgis-wms",
      minzoom: 0,
      maxzoom: 22,
    },
    {
      id: "osm-fallback-basemap",
      type: "raster",
      source: "osm-fallback",
      minzoom: 0,
      maxzoom: 22,
      layout: { visibility: "none" },
    },
  ],
};

// Module-level reference so external code (e.g. integration tests, future
// LayerPanel apply paths) can introspect the map. The web side never
// mutates basemap style spec at runtime — only future agent-driven layers
// (M4) will append/remove layers via map-command handlers (job-0026+).
let activeMap: MapLibreMap | null = null;

export function getActiveMap(): MapLibreMap | null {
  return activeMap;
}

export type SessionStateSubscriber = (p: SessionStatePayload) => void;

// Wire-layer shape: the agent emits `uri` (not `source_url`) per its Python
// ProjectLayerSummary. contracts.ts uses `source_url` (the older TS mirror).
// This local type reads from the actual wire format. Job-0070 will reconcile
// the schema mismatch. (OQ-0068-URI: see report.md)
interface WireLayerSummary {
  layer_id: string;
  name: string;
  layer_type: string;
  uri: string;          // agent wire format (Python `uri` field)
  visible?: boolean;
  opacity?: number;
  // job-0139 — vector layer additions. Optional because raster layers omit them.
  style_preset?: string | null;
  bbox?: [number, number, number, number] | null;
  // job-0175 — inline GeoJSON for vector layers. When present, the client
  // skips the `uri` fetch (which would hit Invariant 5's gs:// guardrail
  // and silently no-op) and renders directly from this FeatureCollection.
  // The agent populates this for every cacheable vector fetcher (see
  // `services/agent/src/grace2_agent/pipeline_emitter.py:add_loaded_layer`).
  // Optional — older session-state snapshots predate this field.
  inline_geojson?: unknown;
  // F94 — dense-vector handling. When the agent's tiled path is enabled it
  // emits a client-reachable vector-tile URL ({z}/{x}/{y}.pbf MVT or a
  // pmtiles:// URL) instead of inline GeoJSON, so MapLibre only draws what is
  // in view. When present this takes precedence over `inline_geojson`.
  vector_tile_url?: string;
  // F94 — geometry family for the tiled source's paint layer (point/line/
  // polygon). The tiled path has no features to classify client-side, so the
  // agent declares the kind. Defaults to "polygon" (the footprint case).
  vector_geom_kind?: string;
  // F94 — vector source-layer name inside the MVT tiles (PMTiles builder uses
  // "vector" by default). Required to address features in a vector source.
  vector_source_layer?: string;
  // F94 — honest density tag when a dense layer was simplified/capped on the
  // inline fallback path. Additive (extra-tolerant): surfaced so the user knows
  // the layer was reduced for performance; never a silent drop.
  vector_density?: {
    strategy: string;
    original_feature_count: number;
    emitted_feature_count: number;
    simplified: boolean;
    capped: boolean;
  };
}

// Extended map-command discriminator: contracts.ts only mirrors the 5 layer-CRUD
// verbs (zoom-to etc. are deferred to M4-M5 per job-0025 scope). Map.tsx
// handles zoom-to from the bus (dev-injection + future WS routing). We use
// a widened local type so the switch is type-safe without editing frozen contracts.ts.
interface ZoomToCommand {
  command: "zoom-to";
  args: { bbox: number[] };
}
// job-0294 follow-on (ux-batch-1 F14): clear the analysis-extent rectangle.
// Emitted by App.tsx on Case exit (activeSession → null) and on opening a Case
// that has no bbox / no zoom-to history, so a prior Case's AOI outline does not
// linger on the map. No args — it removes the single extent source + layers.
interface ClearAnalysisExtentCommand {
  command: "clear-analysis-extent";
}
// ux-batch-1 (F-CASES-CLEAR-ALL): snap the camera back to the default CONUS
// view. Emitted by App.tsx on Case EXIT (to the Cases root) so leaving a Case
// visibly resets the map (camera-only — no extent rectangle, unlike zoom-to).
interface ResetViewCommand {
  command: "reset-view";
}
type WireMapCommand =
  | MapCommandPayload
  | ZoomToCommand
  | ClearAnalysisExtentCommand
  | ResetViewCommand;

// subscribeMapCommand accepts a callback that can handle the wider WireMapCommand.
// The bus pushes MapCommandPayload values which satisfy WireMapCommand at runtime.
export type MapCommandSubscribeFunc = (cb: (p: WireMapCommand) => void) => () => void;

export interface MapViewProps {
  subscribeSessionState?: (cb: SessionStateSubscriber) => () => void;
  subscribeMapCommand?: MapCommandSubscribeFunc;
  /** Light = QGIS Server WMS basemap. Dark = CartoDB DarkMatter raster.
   *  job-0076 bundled enhancement (dark backdrop makes flood overlay obvious). */
  theme?: MapTheme;
}

/**
 * Style-preset → WMS LAYERS value derivation table for upstream tools that
 * emit a bare WMS endpoint (no `?LAYERS=…`) and rely on the client to
 * supply the layer name. Currently used for Iowa State Mesonet NEXRAD
 * (`fetch_nexrad_reflectivity` — job-0102/0105 family) whose LayerURI.uri
 * is `https://…/wms/nexrad/<product>.cgi` with the LAYERS value implicit
 * in the path.
 *
 * job-0171: the producer contract documented in
 * `docs/decisions/layer-emission-contract.md:36` says `ProjectLayerSummary.uri`
 * MUST be a full WMS URL with `LAYERS=` baked in. Several Tier-1
 * data-source atomic tools violated that contract by emitting only the
 * service endpoint. This map is the compatibility shim that recovers the
 * intended LAYERS name from `style_preset`; the long-term fix is for those
 * tools to emit a complete URL (raised as OQ-0171-WMS-URL-CONTRACT).
 *
 * The presets here mirror the values registered in
 * `services/agent/src/grace2_agent/tools/fetch_nexrad_reflectivity.py:111-117`
 * (`_PRODUCT_LAYER_NAME`).
 */
const STYLE_PRESET_TO_WMS_LAYERS: Record<string, string> = {
  // job-0171 live diagnosis (evidence/iowa_capabilities_audit.txt): the
  // Iowa Mesonet WMS does NOT publish `nexrad-{product}-wmst` layers — that
  // value in the agent tool's `_PRODUCT_LAYER_NAME` table is wrong. The
  // EPSG:3857 (Web Mercator) layer name follows the legacy `-900913`
  // convention (the original Web-Mercator EPSG code, kept for back-compat
  // by Iowa Mesonet). We use the `-900913` suffix because MapLibre's raster
  // source requests tiles in EPSG:3857. Tracked as OQ-0171-NEXRAD-LAYER-NAME.
  nexrad_n0r: "nexrad-n0r-900913",
  nexrad_n0q: "nexrad-n0q-900913",
  nexrad_vil: "nexrad-vil-900913",
};

/**
 * Build the WMS tile URL for a given base WMS URL. MapLibre substitutes
 * `{bbox-epsg-3857}` per tile.
 *
 * Invariant 4: QGIS Server renders; client just registers the URL.
 *
 * Contract (per `docs/decisions/layer-emission-contract.md:36`): the
 * base URL is expected to already include `?` + the WMS service params
 * MAP and LAYERS. job-0171 diagnosis (evidence/radar_diag.json) shows
 * the Iowa State Mesonet NEXRAD tool emits the bare `*.cgi` endpoint
 * without either `?` or `LAYERS=`, which means this helper used to
 * produce malformed URLs like `…n0r.cgi&SERVICE=WMS&…&LAYERS=` (no
 * `LAYERS` value) that the Iowa Mesonet WMS rejects as a 400.
 *
 * This helper now defensively normalises:
 *   1. Use `?` as separator when the base URL has no `?` yet, `&` otherwise.
 *   2. If the base URL is missing a `LAYERS=` param, fall back to the
 *      `style_preset → STYLE_PRESET_TO_WMS_LAYERS` lookup. Logs a warn
 *      when neither is present so the diagnosis is loud.
 *   3. Add the per-tile WMS GetMap params MapLibre's raster source needs.
 */
export function buildWmsTileUrl(wmsUrl: string, stylePreset?: string | null): string {
  // sprint-14-aws (job-0290): the AWS agent publishes rasters as ready XYZ
  // tile TEMPLATES (TiTiler — contains {z}/{x}/{y}). Pass them through
  // untouched: appending WMS params to an XYZ template would 400 every tile.
  if (wmsUrl.includes("{z}")) {
    // sprint-14-aws (job-0296): on the HTTPS CloudFront edge, rewrite a legacy
    // http://<ip>:8080 TiTiler origin (baked into pre-cutover layer URIs) to the
    // public base so persisted tiles aren't mixed-content-blocked. CloudFront's
    // /cog/* behavior routes to TiTiler. No-op when VITE_GRACE2_PUBLIC_BASE is
    // unset (publicTileBase()===null) — byte-identical to the http-site path.
    const base = publicTileBase();
    if (base) return wmsUrl.replace(/^https?:\/\/[^/]+:8080/, base);
    return wmsUrl;
  }
  // job-0255: route overlay WMS URLs through the agent proxy when
  // VITE_QGIS_PROXY_BASE is set (no-op otherwise — byte-identical).
  wmsUrl = applyQgisProxy(wmsUrl);
  const sep = wmsUrl.includes("?") ? "&" : "?";
  let layersParam = "";
  // The upstream URL may already contain LAYERS=. If it doesn't, attempt to
  // synthesise one from the style preset so the tile request is actually
  // valid (otherwise the WMS server 400s and MapLibre silently paints
  // nothing — the user-reported symptom).
  if (!/[?&]LAYERS=/i.test(wmsUrl)) {
    const layers = stylePreset ? STYLE_PRESET_TO_WMS_LAYERS[stylePreset] : undefined;
    if (layers) {
      layersParam = `&LAYERS=${encodeURIComponent(layers)}`;
    } else {
      // No LAYERS in URL and no preset mapping. Tile fetch is doomed; log
      // loudly so this is diagnosable without needing the network panel.
      // We still emit the URL so a future fix can pick up cleanly without
      // changing the call sites (defense in depth, not silent suppression).
      // eslint-disable-next-line no-console
      console.warn(
        "[Map] buildWmsTileUrl: WMS URL has no LAYERS= and no known style preset; tile fetch will likely 400. " +
          "See OQ-0171-WMS-URL-CONTRACT. uri=" + wmsUrl + " style_preset=" + String(stylePreset),
      );
    }
  }
  return `${wmsUrl}${sep}SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&CRS=EPSG:3857&FORMAT=image%2Fpng&TRANSPARENT=true&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256&STYLES=${layersParam}`;
}

// Layer + source IDs for the swappable basemap. The light basemap source is
// the QGIS Server WMS proxy (already in the seed style); the dark basemap
// source is added/removed at runtime when the theme changes.
const BASEMAP_LAYER_ID = "qgis-basemap";
const BASEMAP_SOURCE_ID = "qgis-wms";
const DARK_BASEMAP_LAYER_ID = "carto-dark-basemap";
const DARK_BASEMAP_SOURCE_ID = "carto-dark";

// job-0294 — "analysis extent" rectangle. When the agent emits a `zoom-to`
// map-command with a bbox, we ALSO outline that extent as a styled rectangle so
// the user sees exactly what area is being measured. A SINGLE extent rectangle
// (replace-on-new-bbox) is the v0.1 contract — the source is the same
// map-command the camera consumes, so persisted-case reopen (App.tsx replays
// the last zoom-to through the bus) redraws it for free. Thin dashed accent
// stroke, faint fill.
const ANALYSIS_EXTENT_SOURCE_ID = "grace2-analysis-extent";
const ANALYSIS_EXTENT_FILL_LAYER_ID = "grace2-analysis-extent-fill";
const ANALYSIS_EXTENT_LINE_LAYER_ID = "grace2-analysis-extent-line";

/**
 * INCIDENT FIX 2026-06-16 — hung-tile resilience. The reconcile + layer-add
 * paths gated on ``map.isStyleLoaded()``, which maplibre-gl returns false while
 * ANY source cache is still loading. A single HUNG raster source (e.g. a
 * vector .fgb wrongly published behind TiTiler's /cog raster face — its tiles
 * never resolve) made ``isStyleLoaded()`` false PERMANENTLY, which froze the
 * whole reconcile loop: NO overlays painted, removals didn't run, the AOI
 * never drew (the "layers in panel, blank map, hit-or-miss" incident).
 *
 * Fix: latch readiness once the style spec has loaded a single time. After the
 * first ``isStyleLoaded()===true`` (or the map's ``load`` event), addSource /
 * addLayer are safe regardless of whether some tiles are still loading or hung,
 * so we stop gating on the tile-sensitive ``isStyleLoaded()`` and use the latch
 * instead. The latch lives on the map instance so both the MapView effect and
 * the module-level ``addVectorLayer`` (which only receives ``m``) can read it.
 */
type ReadyMap = MapLibreMap & { __grace2StyleReady?: boolean };
export function mapStyleReady(m: MapLibreMap): boolean {
  const rm = m as ReadyMap;
  try {
    if (m.isStyleLoaded()) {
      rm.__grace2StyleReady = true;
      return true;
    }
  } catch {
    return false;
  }
  return rm.__grace2StyleReady === true;
}

/**
 * Async vector-layer registration (job-0139). Fetches the layer's GeoJSON
 * (or FlatGeobuf-converted-to-GeoJSON), adds a `geojson` source, and adds an
 * appropriate paint layer based on geometry kind. Generation-guarded so a
 * remove-before-resolve race terminates cleanly without leaving an orphan
 * source on the map.
 *
 * Why this is exported (`MapView`-local closure would be cleaner): the
 * function captures several refs as parameters so it can be exercised in
 * isolation by unit tests without rendering a full MapView. Sole call site
 * is the apply loop inside MapView's session-state effect.
 *
 * Invariant 1: every coordinate painted on the map comes from `fc.features`
 * — we never compute geometry client-side.
 */
export async function addVectorLayer(
  m: MapLibreMap,
  layer: {
    layer_id: string;
    uri: string;
    opacity?: number;
    visible?: boolean;
    style_preset?: string | null;
    /** job-0175: inline GeoJSON FeatureCollection from the agent. When present
     *  the client renders from this directly, bypassing the `uri` fetch path
     *  that would otherwise hit the gs:// guardrail in `fetchVectorAsGeoJson`
     *  (Invariant 5) and silently no-op. */
    inline_geojson?: unknown;
  },
  generation: number,
  fetchGenRef: { current: Map<string, number> },
  geomKindRef: { current: Map<string, VectorGeomKind> },
  addedSourceIdsRef: { current: Set<string> },
): Promise<void> {
  const opacity = layer.opacity ?? 1;
  const visible = layer.visible !== false;
  const color = resolveVectorColor(layer.layer_id, layer.style_preset);

  // Debug-only console.log behind import.meta.env.DEV (matches existing
  // diagnostic-seam pattern). Helps the Playwright capture confirm the
  // vector branch was actually entered.
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.log(`[MapView] addVectorLayer start: ${layer.layer_id} gen=${generation} inline=${layer.inline_geojson !== undefined}`);
  }
  let fc;
  let geomKind: VectorGeomKind;
  // job-0175: prefer inline GeoJSON over URI fetch. The agent populates
  // `inline_geojson` for every vector layer it can read from GCS; falling
  // back to URI is preserved for layers the agent could not inline (failure
  // is logged + the row still appears in the LayerPanel without rendering).
  if (layer.inline_geojson !== undefined && layer.inline_geojson !== null) {
    try {
      const result = vectorResultFromInlineGeoJson(layer.inline_geojson);
      fc = result.featureCollection;
      geomKind = result.geomKind;
      if (import.meta.env.DEV) {
        // eslint-disable-next-line no-console
        console.log(
          `[MapView] addVectorLayer inline-geojson hit: ${layer.layer_id} features=${fc.features.length} kind=${geomKind}`,
        );
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn(`[MapView] inline GeoJSON parse failed for ${layer.layer_id}:`, err);
      if (addedSourceIdsRef.current.has(layer.layer_id)) {
        addedSourceIdsRef.current.delete(layer.layer_id);
      }
      return;
    }
  } else {
    try {
      const result = await fetchVectorAsGeoJson(layer.uri);
      fc = result.featureCollection;
      geomKind = result.geomKind;
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn(`[MapView] vector fetch failed for ${layer.layer_id}:`, err);
      // Release the slot so a future session-state push with the same layer_id
      // can retry.
      if (addedSourceIdsRef.current.has(layer.layer_id)) {
        addedSourceIdsRef.current.delete(layer.layer_id);
      }
      return;
    }
  }

  // Race-guard: if a remove or re-add happened during the fetch, the
  // generation counter advanced. Bail out cleanly.
  const currentGen = fetchGenRef.current.get(layer.layer_id) ?? -1;
  if (currentGen !== generation) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.log(`[MapView] addVectorLayer abort (gen): ${layer.layer_id} expected=${generation} actual=${currentGen}`);
    }
    return;
  }
  // Race-guard: addedSourceIdsRef may have been cleared by removal.
  if (!addedSourceIdsRef.current.has(layer.layer_id)) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.log(`[MapView] addVectorLayer abort (removed): ${layer.layer_id}`);
    }
    return;
  }
  // If the map has been torn down (e.g. component unmount during fetch),
  // there's nothing to add to. The MapLibre instance throws on calls after
  // remove(). INCIDENT FIX 2026-06-16: gate on mapStyleReady (a one-time latch)
  // NOT raw isStyleLoaded() — a hung sibling raster tile keeps isStyleLoaded()
  // false forever and would block this vector add (which renders from inline
  // GeoJSON and does not even need tiles) indefinitely. Once the style has
  // loaded once, proceed.
  let styleLoaded = false;
  try {
    styleLoaded = mapStyleReady(m);
  } catch {
    return;
  }
  if (!styleLoaded) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.log(`[MapView] addVectorLayer defer (style not loaded): ${layer.layer_id}`);
    }
    // The MapLibre style is mid-load — typically because a SIBLING vector
    // layer's addSource we just kicked off triggered tile resolution, or
    // the basemap's WMS tiles are still resolving. We must NOT abandon the
    // layer (otherwise a multi-layer Case 1 push only lands the first one).
    // Chain retries via m.once("idle", ...) until either the style settles
    // or the generation guard signals the layer was removed.
    //
    // Why m.once instead of a setTimeout: idle fires exactly when all
    // pending source/tile requests settle, which is the cheapest accurate
    // "ready" signal MapLibre exposes. Each retry guards against runaway
    // chains by capping at MAX_RETRIES.
    const MAX_RETRIES = 20;
    let attempt = 0;
    const retry = () => {
      attempt += 1;
      // Race-recheck guards before touching the map.
      if ((fetchGenRef.current.get(layer.layer_id) ?? -1) !== generation) return;
      if (!addedSourceIdsRef.current.has(layer.layer_id)) return;
      let nowLoaded = false;
      try { nowLoaded = m.isStyleLoaded() ?? false; } catch { return; }
      if (!nowLoaded) {
        if (attempt < MAX_RETRIES) {
          m.once("idle", retry);
        } else if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.warn(`[MapView] addVectorLayer giving up after ${MAX_RETRIES} retries: ${layer.layer_id}`);
        }
        return;
      }
      if (import.meta.env.DEV) {
        // eslint-disable-next-line no-console
        console.log(`[MapView] addVectorLayer addSource (retry ${attempt}): ${layer.layer_id} kind=${geomKind} features=${fc.features.length}`);
      }
      registerVectorOnMap(m, layer, fc, geomKind, color, opacity, visible, geomKindRef);
    };
    m.once("idle", retry);
    return;
  }
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.log(`[MapView] addVectorLayer addSource (sync): ${layer.layer_id} kind=${geomKind} features=${fc.features.length}`);
  }
  registerVectorOnMap(m, layer, fc, geomKind, color, opacity, visible, geomKindRef);
}

/**
 * Inner registration helper — adds a GeoJSON source + the right paint layer
 * to the map. Pure side-effect; no race-guard logic (the caller handles
 * those before invoking).
 *
 * job-0146 additions:
 *   - Pelicun damage polygon path: uses ds_mean choropleth expression (Part 2)
 *   - POLYGON_FILL_OPACITY constant (0.4) for basemap readability (Part 3)
 *   - POLYGON_STROKE_WIDTH constant (1.5px) for polygon edge visibility (Part 3)
 *   - Cluster source for dense point layers >CLUSTER_THRESHOLD features (Part 4)
 */
function registerVectorOnMap(
  m: MapLibreMap,
  layer: { layer_id: string; style_preset?: string | null },
  fc: FeatureCollection,
  geomKind: VectorGeomKind,
  color: string,
  opacity: number,
  visible: boolean,
  geomKindRef: { current: Map<string, VectorGeomKind> },
): void {
  // Add the GeoJSON source. For dense point layers (>CLUSTER_THRESHOLD features),
  // enable MapLibre clustering so thousands of GBIF/iNat/eBird points don't
  // paint as individual overlapping circles at low zoom (Part 4).
  const isPointLayer = geomKind === "point";
  const isDense = isPointLayer && fc.features.length > CLUSTER_THRESHOLD;

  if (isDense) {
    m.addSource(layer.layer_id, {
      type: "geojson",
      data: fc,
      cluster: true,
      clusterRadius: CLUSTER_RADIUS,
      clusterMaxZoom: 14, // clusters disappear above z14 → individual points show
    });
  } else {
    m.addSource(layer.layer_id, {
      type: "geojson",
      data: fc,
    });
  }

  // Add the paint layer. We place vector overlays at the TOP of the stack
  // (no beforeId), matching the raster-overlay convention. Future enhancement:
  // place beneath labels using a known beforeId (e.g. "waterway-label")
  // when one is detected in the active style.
  if (geomKind === "point") {
    if (isDense) {
      // Cluster circle layer (shows aggregate circles with count text).
      m.addLayer({
        id: `${layer.layer_id}-clusters`,
        type: "circle",
        source: layer.layer_id,
        filter: ["has", "point_count"],
        paint: {
          "circle-radius": [
            "step",
            ["get", "point_count"],
            12, 10,   // < 10 points → r12
            18, 100,  // 10–99 points → r18
            24,       // ≥100 points → r24
          ],
          "circle-color": color,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.5,
          "circle-opacity": opacity * 0.85,
        },
        layout: { visibility: visible ? "visible" : "none" },
      });
      // Cluster count label layer.
      m.addLayer({
        id: `${layer.layer_id}-cluster-count`,
        type: "symbol",
        source: layer.layer_id,
        filter: ["has", "point_count"],
        layout: {
          "text-field": "{point_count_abbreviated}",
          "text-size": 11,
          "text-font": ["Open Sans Regular"],
          visibility: visible ? "visible" : "none",
        },
        paint: {
          "text-color": "#ffffff",
        },
      });
      // Individual unclustered points at high zoom.
      m.addLayer({
        id: layer.layer_id,
        type: "circle",
        source: layer.layer_id,
        filter: ["!", ["has", "point_count"]],
        paint: {
          "circle-radius": 5,
          "circle-color": color,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
          "circle-opacity": opacity,
          "circle-stroke-opacity": opacity,
        },
        layout: { visibility: visible ? "visible" : "none" },
      });
    } else {
      m.addLayer({
        id: layer.layer_id,
        type: "circle",
        source: layer.layer_id,
        paint: {
          "circle-radius": 5,
          "circle-color": color,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
          "circle-opacity": opacity,
          "circle-stroke-opacity": opacity,
        },
        layout: { visibility: visible ? "visible" : "none" },
      });
    }
  } else if (geomKind === "line") {
    m.addLayer({
      id: layer.layer_id,
      type: "line",
      source: layer.layer_id,
      paint: {
        "line-color": color,
        "line-width": 2,
        "line-opacity": opacity,
      },
      layout: { visibility: visible ? "visible" : "none" },
    });
  } else if (geomKind === "polygon") {
    // Pelicun damage: apply ds_mean choropleth gradient expression (Part 2).
    // All other polygons: flat fill with POLYGON_FILL_OPACITY (Part 3).
    const fillColor = isPelicunDamageLayer(layer.style_preset)
      ? buildDsMeanExpression()
      : color;

    m.addLayer({
      id: layer.layer_id,
      type: "fill",
      source: layer.layer_id,
      paint: {
        // MapLibre fill-color accepts expression arrays natively.
        "fill-color": fillColor as string,
        // Reduced fill opacity (0.4) so basemap labels stay readable
        // underneath polygon fills (Part 3). Pelicun uses 0.7 so the
        // damage gradient is visually prominent.
        "fill-opacity": isPelicunDamageLayer(layer.style_preset)
          ? opacity * 0.7
          : opacity * POLYGON_FILL_OPACITY,
        // Subtle stroke softens the CDP-rectangle look while keeping edges
        // distinguishable (Part 3 / Pelicun "less rectangular" ask).
        "fill-outline-color": color,
      },
      layout: { visibility: visible ? "visible" : "none" },
    });
    // Add a separate line layer for the polygon stroke so we can set stroke
    // width (fill-outline-color only draws 1px; line layer gives us 1.5px).
    m.addLayer({
      id: `${layer.layer_id}-outline`,
      type: "line",
      source: layer.layer_id,
      paint: {
        "line-color": color,
        "line-width": POLYGON_STROKE_WIDTH,
        "line-opacity": opacity * 0.6,
      },
      layout: { visibility: visible ? "visible" : "none" },
    });
  } else {
    // Unknown geometry — leave the source registered but skip the paint
    // layer. The LayerPanel still shows the row (driven by session-state),
    // and the next style-preset addition can rescue.
    // eslint-disable-next-line no-console
    console.warn(`[MapView] unknown geometry kind for ${layer.layer_id}; skipping paint layer`);
  }

  geomKindRef.current.set(layer.layer_id, geomKind);
}

/**
 * F94 — register a DENSE vector layer as a MapLibre VECTOR-TILE source + paint
 * layer, so the browser fetches and draws ONLY the tiles in the current
 * viewport instead of one giant inline GeoJSON FeatureCollection (the OSM
 * building-footprint lag NATE reported). This is the agent's PREFERRED dense
 * path; it activates when a wire layer carries `vector_tile_url`.
 *
 * The url is either a `{z}/{x}/{y}.pbf` MVT template (plain MapLibre `vector`
 * source, no extra dependency) or a `pmtiles://...` URL (requires the pmtiles
 * protocol to be registered with MapLibre — a follow-on once a serving face
 * exists). Either way the source `type` is `vector`; only the `tiles`/`url`
 * field differs. We reuse the SAME geometry-kind paint styling as the inline
 * path (fill/line/circle) for visual consistency.
 *
 * Pure side-effect; the caller handles race-guards + style-ready gating
 * (same contract as `registerVectorOnMap`).
 */
export function registerVectorTileLayer(
  m: MapLibreMap,
  layer: {
    layer_id: string;
    vector_tile_url: string;
    vector_geom_kind?: string;
    vector_source_layer?: string;
    style_preset?: string | null;
    opacity?: number;
    visible?: boolean;
  },
  geomKindRef: { current: Map<string, VectorGeomKind> },
): void {
  const opacity = layer.opacity ?? 1;
  const visible = layer.visible !== false;
  const color = resolveVectorColor(layer.layer_id, layer.style_preset);
  const geomKind = (
    ["point", "line", "polygon"].includes(layer.vector_geom_kind ?? "")
      ? layer.vector_geom_kind
      : "polygon"
  ) as VectorGeomKind;
  const sourceLayer = layer.vector_source_layer || "vector";
  const url = layer.vector_tile_url;

  // pmtiles:// URLs are consumed via the pmtiles protocol's `url` field;
  // {z}/{x}/{y} templates are a plain `tiles` array. MapLibre source type is
  // `vector` in both cases.
  const vectorSource: maplibregl.VectorSourceSpecification = url.startsWith(
    "pmtiles://",
  )
    ? { type: "vector", url }
    : { type: "vector", tiles: [url], minzoom: 0, maxzoom: 14 };
  m.addSource(layer.layer_id, vectorSource);

  if (geomKind === "point") {
    m.addLayer({
      id: layer.layer_id,
      type: "circle",
      source: layer.layer_id,
      "source-layer": sourceLayer,
      paint: {
        "circle-radius": 5,
        "circle-color": color,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1,
        "circle-opacity": opacity,
        "circle-stroke-opacity": opacity,
      },
      layout: { visibility: visible ? "visible" : "none" },
    } as unknown as Parameters<MapLibreMap["addLayer"]>[0]);
  } else if (geomKind === "line") {
    m.addLayer({
      id: layer.layer_id,
      type: "line",
      source: layer.layer_id,
      "source-layer": sourceLayer,
      paint: {
        "line-color": color,
        "line-width": 2,
        "line-opacity": opacity,
      },
      layout: { visibility: visible ? "visible" : "none" },
    } as unknown as Parameters<MapLibreMap["addLayer"]>[0]);
  } else {
    const fillColor = isPelicunDamageLayer(layer.style_preset)
      ? buildDsMeanExpression()
      : color;
    m.addLayer({
      id: layer.layer_id,
      type: "fill",
      source: layer.layer_id,
      "source-layer": sourceLayer,
      paint: {
        "fill-color": fillColor as string,
        "fill-opacity": isPelicunDamageLayer(layer.style_preset)
          ? opacity * 0.7
          : opacity * POLYGON_FILL_OPACITY,
        "fill-outline-color": color,
      },
      layout: { visibility: visible ? "visible" : "none" },
    } as unknown as Parameters<MapLibreMap["addLayer"]>[0]);
    m.addLayer({
      id: `${layer.layer_id}-outline`,
      type: "line",
      source: layer.layer_id,
      "source-layer": sourceLayer,
      paint: {
        "line-color": color,
        "line-width": POLYGON_STROKE_WIDTH,
        "line-opacity": opacity * 0.6,
      },
      layout: { visibility: visible ? "visible" : "none" },
    } as unknown as Parameters<MapLibreMap["addLayer"]>[0]);
  }

  geomKindRef.current.set(layer.layer_id, geomKind);
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.log(
      `[MapView] registerVectorTileLayer: ${layer.layer_id} kind=${geomKind} url=${url}`,
    );
  }
}

// --- Layer-control application helpers (job-0258) ----------------------- //
//
// ROOT-CAUSE CONTEXT: until job-0258, the LayerPanel's user controls
// (opacity slider / visibility checkbox / drag-reorder) dispatched ONLY to
// the panel's local reducer ("M3 local intent" stubs, LayerPanel.tsx) and
// never reached the MapLibre instance — and Map.tsx had no `moveLayer` call
// anywhere, so stack reordering was impossible even for agent-driven
// `set-layer-order` envelopes. These exported helpers are the single shared
// "apply to map" path, used by BOTH the session-state reconciliation loop
// and the map-command subscription that the LayerPanel now feeds through
// the App bus.
//
// One logical GRACE-2 layer (`layer_id`) can own several MapLibre layers:
//   - dense point layers:  `${id}-clusters`, `${id}-cluster-count`, `${id}`
//   - polygon layers:      `${id}`, `${id}-outline`
//   - raster/line/points:  `${id}` only
// (see `registerVectorOnMap` above). Every control operation must address
// the whole group, otherwise outlines/clusters get visually orphaned.

/**
 * Existing MapLibre layer ids belonging to one logical layer, in
 * bottom-to-top paint order (the order `registerVectorOnMap` added them).
 */
export function layerGroupMemberIds(m: MapLibreMap, layerId: string): string[] {
  const candidates = [
    `${layerId}-clusters`,
    `${layerId}-cluster-count`,
    layerId,
    `${layerId}-outline`,
  ];
  return candidates.filter((id) => {
    try {
      return Boolean(m.getLayer(id));
    } catch {
      return false;
    }
  });
}

/**
 * Apply a 0..1 opacity to every paint property of the layer group, using the
 * same per-geometry multipliers `registerVectorOnMap` used at creation time
 * (cluster circles ×0.85, polygon fill ×POLYGON_FILL_OPACITY or ×0.7 for
 * Pelicun damage, outline ×0.6). Raster/unknown falls through to
 * `raster-opacity` — the original flood-COG path.
 */
export function applyLayerOpacity(
  m: MapLibreMap,
  layerId: string,
  opacity: number,
  geomKind: VectorGeomKind | undefined,
  stylePreset?: string | null,
): void {
  if (!m.getLayer(layerId)) return;
  if (geomKind === "point") {
    m.setPaintProperty(layerId, "circle-opacity", opacity);
    m.setPaintProperty(layerId, "circle-stroke-opacity", opacity);
    if (m.getLayer(`${layerId}-clusters`)) {
      m.setPaintProperty(`${layerId}-clusters`, "circle-opacity", opacity * 0.85);
    }
    if (m.getLayer(`${layerId}-cluster-count`)) {
      m.setPaintProperty(`${layerId}-cluster-count`, "text-opacity", opacity);
    }
  } else if (geomKind === "line") {
    m.setPaintProperty(layerId, "line-opacity", opacity);
  } else if (geomKind === "polygon") {
    const polyOpacity = isPelicunDamageLayer(stylePreset)
      ? opacity * 0.7
      : opacity * POLYGON_FILL_OPACITY;
    m.setPaintProperty(layerId, "fill-opacity", polyOpacity);
    m.setPaintProperty(layerId, "fill-outline-color", resolveVectorColor(layerId, stylePreset));
    if (m.getLayer(`${layerId}-outline`)) {
      m.setPaintProperty(`${layerId}-outline`, "line-opacity", opacity * 0.6);
    }
  } else {
    // Raster or unknown — preserve the raster path so the flood-depth COG
    // keeps responding (the original demo symptom).
    m.setPaintProperty(layerId, "raster-opacity", opacity);
  }
}

/** Flip layout visibility on every member of the layer group. */
export function applyLayerVisibility(
  m: MapLibreMap,
  layerId: string,
  visible: boolean,
): void {
  for (const id of layerGroupMemberIds(m, layerId)) {
    m.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
  }
}

/**
 * Re-stack overlay layer groups to match `layerIdsTopFirst` (the LayerPanel /
 * `set-layer-order` convention: first element renders ON TOP). MapLibre's
 * `moveLayer(id)` with no beforeId moves a layer to the top of the stack, so
 * iterating bottom-first pulls each group to the top in turn — the last
 * (top-of-panel) group ends up painted last, i.e. on top. Basemap layers are
 * never named in the command, so they stay at the bottom. Group members move
 * in their internal bottom-to-top order so sublayers keep their relative
 * stacking (e.g. cluster counts above cluster circles).
 */
export function applyLayerOrder(m: MapLibreMap, layerIdsTopFirst: string[]): void {
  const bottomFirst = [...layerIdsTopFirst].reverse();
  for (const layerId of bottomFirst) {
    for (const member of layerGroupMemberIds(m, layerId)) {
      try {
        m.moveLayer(member);
      } catch {
        // Mid-removal race (style mutation between getLayer and moveLayer) —
        // skip; the next session-state reconciliation restores consistency.
      }
    }
  }
}

/**
 * job-0294 — draw (or replace) the single "analysis extent" rectangle for a
 * bbox `[minLon, minLat, maxLon, maxLat]`. Idempotent: the first call adds the
 * GeoJSON source + a faint fill layer + a dashed accent outline; subsequent
 * calls call `setData` so the extent REPLACES (one extent at a time, v0.1).
 *
 * The bbox comes from the same `zoom-to` map-command the camera consumes, so no
 * agent change is needed; case-reopen replays the last zoom-to (App.tsx) and
 * redraws the rectangle for free. Pure rendering — no numbers are computed
 * (Invariant 1): the geometry is built verbatim from the received bbox corners.
 */
export function drawAnalysisExtent(
  m: MapLibreMap,
  bbox: [number, number, number, number],
): void {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  const ring: [number, number][] = [
    [minLon, minLat],
    [maxLon, minLat],
    [maxLon, maxLat],
    [minLon, maxLat],
    [minLon, minLat],
  ];
  const data: Feature<Polygon> = {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [ring] },
  };

  // AWS-migration hardening (bbox track): make this idempotent AND
  // partial-state tolerant. A prior call that threw mid-mutation (the live
  // failure mode — addSource succeeded but an addLayer threw, or the camera
  // animation churned the style between the two addLayer calls) can leave the
  // source present but one/both layers missing. The old code early-returned
  // the moment the source existed, so a half-built extent never self-healed
  // and the dashed rectangle was permanently absent. Now: (1) swap data on the
  // existing source, then (2) re-add ANY missing layer; on a clean first call
  // both source and layers are added. Each add is existence-guarded so a
  // duplicate-id throw cannot abort the function.
  const existing = m.getSource(ANALYSIS_EXTENT_SOURCE_ID) as
    | maplibregl.GeoJSONSource
    | undefined;
  if (existing) {
    // Replace-on-new-bbox: swap the data; layers (re-)asserted below.
    existing.setData(data);
  } else {
    m.addSource(ANALYSIS_EXTENT_SOURCE_ID, { type: "geojson", data });
  }

  // job-0321 (F40) — OUTLINE-ONLY AOI. The AOI rectangle previously painted a
  // translucent fill (#4D96FF @ 0.06) over the whole extent, which tinted every
  // layer rendered beneath it (the user-reported "blue wash over my layers").
  // We now draw the dashed outline ONLY — no fill layer is added. The fill
  // LAYER ID constant + the clearAnalysisExtent() removal guard are KEPT intact
  // so a stale fill left over from a previous app version / partial style still
  // gets torn down cleanly (idempotent, partial-state tolerant).
  //
  // Thin dashed accent outline — the primary "here's the measured extent" cue.
  if (!m.getLayer(ANALYSIS_EXTENT_LINE_LAYER_ID)) {
    m.addLayer({
      id: ANALYSIS_EXTENT_LINE_LAYER_ID,
      type: "line",
      source: ANALYSIS_EXTENT_SOURCE_ID,
      paint: {
        "line-color": "#4D96FF",
        "line-width": 1.5,
        "line-dasharray": [3, 2],
        "line-opacity": 0.9,
      },
    });
  }
}

/**
 * ux-batch-1 (F14) — remove the analysis-extent rectangle (fill + outline +
 * source). Inverse of drawAnalysisExtent. Idempotent and partial-state
 * tolerant: each removal is existence-guarded so a half-built extent (source
 * present, a layer missing) still clears cleanly and a missing extent is a
 * no-op. Layers must be removed before their source (MapLibre rejects removing
 * a source still referenced by a layer).
 */
export function clearAnalysisExtent(m: MapLibreMap): void {
  if (m.getLayer(ANALYSIS_EXTENT_FILL_LAYER_ID)) {
    m.removeLayer(ANALYSIS_EXTENT_FILL_LAYER_ID);
  }
  if (m.getLayer(ANALYSIS_EXTENT_LINE_LAYER_ID)) {
    m.removeLayer(ANALYSIS_EXTENT_LINE_LAYER_ID);
  }
  if (m.getSource(ANALYSIS_EXTENT_SOURCE_ID)) {
    m.removeSource(ANALYSIS_EXTENT_SOURCE_ID);
  }
}

// --- Region-disambiguation choropleth (state-bbox-fallback narrowing) ----- //
//
// When a `geocode_location` result snaps to a whole-state bbox, the agent
// offers a narrower county pick (region-choice-request). The candidate counties
// render as a tappable CHOROPLETH on the map, SYNCED with the in-chat
// RegionPickerCard list via the region-choice bus: hovering/selecting a region
// in either surface highlights its polygon, and tapping a polygon picks it
// (same reply path as clicking the card row). Each candidate carries an
// EPSG:4326 bbox (`RegionCandidate.bbox`); we draw one rectangle polygon per
// candidate keyed by `region_id`. Invariant 1: the geometry is built verbatim
// from the received candidate bboxes — no geography is computed.
//
// Reuses the same MapLibre GeoJSON fill+line vector pattern the analysis-extent
// rectangle / vector layers use (Invariant 4: the client just registers
// sources/layers). Per-feature highlight is driven by `feature-state`
// (hovered / selected) set on the source so a hover repaints only the touched
// polygon without re-issuing the whole FeatureCollection.

export const REGION_CHOICE_SOURCE_ID = "grace2-region-choice";
export const REGION_CHOICE_FILL_LAYER_ID = "grace2-region-choice-fill";
export const REGION_CHOICE_LINE_LAYER_ID = "grace2-region-choice-line";

const REGION_ACCENT = "#3b82f6"; // matches RegionPickerCard ACCENT (blue)

/**
 * Build the candidate county choropleth FeatureCollection from the request's
 * candidates. One rectangle Polygon per candidate, keyed by `region_id` as the
 * feature id (so `setFeatureState` can target it) AND in `properties.region_id`
 * + `properties.name` (so a tap hit-test reads them back). Pure — exported for
 * unit testing.
 */
export function buildRegionChoiceGeoJson(
  candidates: RegionCandidate[],
): FeatureCollection<Polygon> {
  const features: Feature<Polygon>[] = candidates.map((c) => {
    const [minLon, minLat, maxLon, maxLat] = c.bbox;
    const ring: [number, number][] = [
      [minLon, minLat],
      [maxLon, minLat],
      [maxLon, maxLat],
      [minLon, maxLat],
      [minLon, minLat],
    ];
    return {
      type: "Feature",
      id: c.region_id,
      properties: { region_id: c.region_id, name: c.name },
      geometry: { type: "Polygon", coordinates: [ring] },
    };
  });
  return { type: "FeatureCollection", features };
}

/**
 * Render (or update) the candidate county choropleth from a region-choice
 * request. Idempotent + partial-state tolerant (mirrors drawAnalysisExtent):
 * swaps the data on an existing source, re-adds any missing layer. The fill
 * uses a feature-state-driven opacity ramp (selected > hovered > base) so the
 * highlighted county pops without re-issuing the data; the line gives the
 * county outline a crisp edge.
 */
export function drawRegionChoropleth(
  m: MapLibreMap,
  candidates: RegionCandidate[],
): void {
  const data = buildRegionChoiceGeoJson(candidates);
  const existing = m.getSource(REGION_CHOICE_SOURCE_ID) as
    | maplibregl.GeoJSONSource
    | undefined;
  if (existing) {
    existing.setData(data);
  } else {
    m.addSource(REGION_CHOICE_SOURCE_ID, {
      type: "geojson",
      data,
      // promoteId so the candidate's region_id is the canonical feature id
      // feature-state targets (a GeoJSON source feature id must be set this way
      // to be addressable by setFeatureState across data swaps).
      promoteId: "region_id",
    });
  }

  if (!m.getLayer(REGION_CHOICE_FILL_LAYER_ID)) {
    m.addLayer({
      id: REGION_CHOICE_FILL_LAYER_ID,
      type: "fill",
      source: REGION_CHOICE_SOURCE_ID,
      paint: {
        "fill-color": REGION_ACCENT,
        // selected (0.42) > hovered (0.30) > base (0.12) — the highlighted
        // county reads as the focus while the rest stay tappable hints.
        "fill-opacity": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          0.42,
          ["boolean", ["feature-state", "hovered"], false],
          0.3,
          0.12,
        ],
      },
    });
  }
  if (!m.getLayer(REGION_CHOICE_LINE_LAYER_ID)) {
    m.addLayer({
      id: REGION_CHOICE_LINE_LAYER_ID,
      type: "line",
      source: REGION_CHOICE_SOURCE_ID,
      paint: {
        "line-color": REGION_ACCENT,
        "line-width": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          2.5,
          ["boolean", ["feature-state", "hovered"], false],
          2,
          1,
        ],
        "line-opacity": 0.9,
      },
    });
  }
}

/**
 * Remove the candidate county choropleth (fill + line + source). Inverse of
 * drawRegionChoropleth. Idempotent + partial-state tolerant; layers removed
 * before their source (MapLibre rejects removing a referenced source).
 */
export function clearRegionChoropleth(m: MapLibreMap): void {
  if (m.getLayer(REGION_CHOICE_FILL_LAYER_ID)) {
    m.removeLayer(REGION_CHOICE_FILL_LAYER_ID);
  }
  if (m.getLayer(REGION_CHOICE_LINE_LAYER_ID)) {
    m.removeLayer(REGION_CHOICE_LINE_LAYER_ID);
  }
  if (m.getSource(REGION_CHOICE_SOURCE_ID)) {
    m.removeSource(REGION_CHOICE_SOURCE_ID);
  }
}

/**
 * Apply the bus-synced hover + selection to the choropleth's feature-state.
 * `prevIds` is the set of region_ids that currently carry a non-default state
 * (so we can clear stale highlights without enumerating every candidate). Pure
 * side effect on the map; returns the new set of region_ids carrying state for
 * the next diff. No-op-safe when the source is absent (mid-teardown).
 */
export function applyRegionChoiceHighlight(
  m: MapLibreMap,
  hoveredId: string | null,
  selectedId: string | null,
  prevIds: Set<string>,
): Set<string> {
  if (!m.getSource(REGION_CHOICE_SOURCE_ID)) return new Set();
  const nextIds = new Set<string>();
  if (hoveredId) nextIds.add(hoveredId);
  if (selectedId) nextIds.add(selectedId);
  // Clear any region that was highlighted but no longer is.
  for (const id of prevIds) {
    if (nextIds.has(id)) continue;
    try {
      m.setFeatureState(
        { source: REGION_CHOICE_SOURCE_ID, id },
        { hovered: false, selected: false },
      );
    } catch {
      /* feature gone mid-swap — ignore */
    }
  }
  // Apply current state to the touched regions.
  for (const id of nextIds) {
    try {
      m.setFeatureState(
        { source: REGION_CHOICE_SOURCE_ID, id },
        { hovered: hoveredId === id, selected: selectedId === id },
      );
    } catch {
      /* feature gone mid-swap — ignore */
    }
  }
  return nextIds;
}

// --- FIX 1 (NATE 2026-06-17) — generic whole-feature tap HIGHLIGHT --------- //
//
// Tapping a vector feature opens FeaturePopup but used to leave the feature
// unmarked, so the user couldn't tell WHICH polygon/line/point they hit. We now
// outline the ENTIRE tapped geometry, GENERICALLY across every vector overlay
// type and geometry, with ONE dedicated highlight source + three paint layers:
//   - a fill layer    → paints the interior of a tapped POLYGON
//   - a line layer    → paints a thick stroke for a tapped LINE *and* the
//                       boundary of a tapped polygon (MapLibre's line layer
//                       renders polygon rings too), so one layer covers both
//   - a circle layer  → paints an enlarged ring for a tapped POINT
// MapLibre only paints the geometry kinds each layer type understands, so a
// single highlight source carrying ONE feature lights up exactly the right
// layer(s) regardless of geometry — no per-overlay-type branching.
//
// The highlight lives in MAP space (a geojson source), so it pans with the map
// and scales with zoom for free (FIX 1 acceptance). It is cleared when the popup
// closes (X / Esc / a no-hit tap) and REPLACED when another feature is tapped.
//
// Invariant 1: the highlight geometry is CLONED verbatim from the tapped
// feature's own geometry — no geography is computed client-side.

export const FEATURE_HIGHLIGHT_SOURCE_ID = "grace2-feature-highlight";
export const FEATURE_HIGHLIGHT_FILL_LAYER_ID = "grace2-feature-highlight-fill";
export const FEATURE_HIGHLIGHT_LINE_LAYER_ID = "grace2-feature-highlight-line";
export const FEATURE_HIGHLIGHT_CIRCLE_LAYER_ID = "grace2-feature-highlight-circle";

// A warm accent distinct from the blue AOI outline / region choropleth so the
// highlight reads as "this is the thing you tapped".
const HIGHLIGHT_ACCENT = "#facc15"; // amber-400

/**
 * Build a single-feature FeatureCollection from a tapped feature's geometry.
 * The geometry is CLONED (structuredClone / JSON round-trip) so a later
 * setData / source teardown can never mutate MapLibre's own feature objects.
 * Returns an empty FeatureCollection when the geometry is absent (defensive —
 * a hit with no geometry simply clears the highlight). Pure — exported for unit
 * testing without a live map.
 */
export function buildHighlightGeoJson(
  geometry: Geometry | null | undefined,
): FeatureCollection {
  if (!geometry) return { type: "FeatureCollection", features: [] };
  let cloned: Geometry;
  try {
    cloned =
      typeof structuredClone === "function"
        ? (structuredClone(geometry) as Geometry)
        : (JSON.parse(JSON.stringify(geometry)) as Geometry);
  } catch {
    return { type: "FeatureCollection", features: [] };
  }
  return {
    type: "FeatureCollection",
    features: [{ type: "Feature", properties: {}, geometry: cloned }],
  };
}

/**
 * Set (or replace) the generic feature highlight to the given geometry.
 * Idempotent + partial-state tolerant (mirrors drawAnalysisExtent): the first
 * call adds the source + the fill/line/circle paint layers; subsequent calls
 * swap the data on the existing source and re-add any layer that went missing.
 * The three layers are ALWAYS present so the SAME highlight source lights up the
 * correct one(s) for whatever geometry it currently holds (polygon → fill+line,
 * line → line, point → circle). Pure side-effect on the map (Invariant 4).
 */
export function setFeatureHighlight(
  m: MapLibreMap,
  geometry: Geometry | null | undefined,
): void {
  const data = buildHighlightGeoJson(geometry);
  const existing = m.getSource(FEATURE_HIGHLIGHT_SOURCE_ID) as
    | maplibregl.GeoJSONSource
    | undefined;
  if (existing) {
    existing.setData(data);
  } else {
    m.addSource(FEATURE_HIGHLIGHT_SOURCE_ID, { type: "geojson", data });
  }

  // Polygon interior — a faint amber wash so the tapped polygon reads as filled.
  if (!m.getLayer(FEATURE_HIGHLIGHT_FILL_LAYER_ID)) {
    m.addLayer({
      id: FEATURE_HIGHLIGHT_FILL_LAYER_ID,
      type: "fill",
      source: FEATURE_HIGHLIGHT_SOURCE_ID,
      paint: {
        "fill-color": HIGHLIGHT_ACCENT,
        "fill-opacity": 0.25,
      },
    });
  }
  // Bold outline for polygons AND a thick stroke for lines (roads / rivers).
  if (!m.getLayer(FEATURE_HIGHLIGHT_LINE_LAYER_ID)) {
    m.addLayer({
      id: FEATURE_HIGHLIGHT_LINE_LAYER_ID,
      type: "line",
      source: FEATURE_HIGHLIGHT_SOURCE_ID,
      paint: {
        "line-color": HIGHLIGHT_ACCENT,
        "line-width": 4,
        "line-opacity": 0.95,
      },
    });
  }
  // Enlarged ring for a tapped point.
  if (!m.getLayer(FEATURE_HIGHLIGHT_CIRCLE_LAYER_ID)) {
    m.addLayer({
      id: FEATURE_HIGHLIGHT_CIRCLE_LAYER_ID,
      type: "circle",
      source: FEATURE_HIGHLIGHT_SOURCE_ID,
      paint: {
        "circle-radius": 10,
        "circle-color": "rgba(0,0,0,0)", // ring only — don't blanket the point
        "circle-stroke-color": HIGHLIGHT_ACCENT,
        "circle-stroke-width": 3,
        "circle-stroke-opacity": 0.95,
      },
    });
  }
}

/**
 * Remove the generic feature highlight (all three layers + source). Inverse of
 * setFeatureHighlight; idempotent + partial-state tolerant. Layers removed
 * before the source (MapLibre rejects removing a referenced source). Used when
 * the popup is dismissed and on map teardown.
 */
export function clearFeatureHighlight(m: MapLibreMap): void {
  for (const id of [
    FEATURE_HIGHLIGHT_FILL_LAYER_ID,
    FEATURE_HIGHLIGHT_LINE_LAYER_ID,
    FEATURE_HIGHLIGHT_CIRCLE_LAYER_ID,
  ]) {
    try {
      if (m.getLayer(id)) m.removeLayer(id);
    } catch {
      /* mid-removal race — best effort */
    }
  }
  try {
    if (m.getSource(FEATURE_HIGHLIGHT_SOURCE_ID)) {
      m.removeSource(FEATURE_HIGHLIGHT_SOURCE_ID);
    }
  } catch {
    /* still referenced / gone — next clear retries */
  }
}

// job-0321 (F43) — legend anchor geometry. The legend (depth-key / colorbar)
// hangs off the BOTTOM EDGE of the AOI bounding box so it reads as the key for
// that AOI. This pure helper projects the two bottom corners of the bbox to
// screen space and returns the bottom-edge MIDPOINT (anchor x) at the LOWEST
// (max-y) of the two projected corners (anchor y) — the bbox can be slightly
// rotated on screen by the Web-Mercator projection at the poles, so we take the
// lower of the two so the legend always clears the box.
//
// Returns null when the bbox is off-screen (the midpoint falls outside the map
// canvas), so the caller can fall back to the bottom-center placement and the
// legend never disappears. Pure — every number comes from MapLibre's project()
// (Invariant 1: the client renders, it never computes geography).
export interface LegendAnchor {
  left: number;
  top: number;
}
export function computeBboxBottomAnchor(
  m: MapLibreMap,
  bbox: [number, number, number, number],
): LegendAnchor | null {
  // Only the bottom edge ([minLat]) is needed — maxLat is intentionally unused.
  const [minLon, minLat, maxLon] = bbox;
  let bl: { x: number; y: number };
  let br: { x: number; y: number };
  try {
    bl = m.project([minLon, minLat]);
    br = m.project([maxLon, minLat]);
  } catch {
    return null;
  }
  const left = (bl.x + br.x) / 2;
  // Anchor at the lower of the two projected bottom corners so the legend
  // clears the box edge regardless of slight projection skew.
  const top = Math.max(bl.y, br.y);

  // Off-screen test: if the anchor midpoint is outside the visible canvas,
  // signal the caller to fall back to bottom-center (legend never vanishes).
  let size: { x: number; y: number } | null = null;
  try {
    const c = m.getCanvas();
    if (c) size = { x: c.clientWidth, y: c.clientHeight };
  } catch {
    size = null;
  }
  if (size) {
    if (left < 0 || left > size.x || top < 0 || top > size.y) return null;
  }
  return { left, top };
}

// --- FIX 4 (NATE 2026-06-17) — legend WIDTH sized to the AOI bbox on-screen -- //
//
// The colorbar was a static 320px. We now size its width to the AOI bbox's
// ON-SCREEN east-west extent: project the bbox's two bottom corners and take the
// horizontal pixel distance between them. That makes the colorbar SPAN the box
// and SHRINK as you zoom out (the bbox gets smaller on screen), reading as the
// physical key for that AOI. Clamped to a sane min (so it never becomes an
// illegible sliver) and to the viewport width minus margins (so it never
// overflows). Returns null when the bbox can't be projected (off-screen / no
// canvas) so the caller falls back to the static 320 width. Pure — every number
// comes from MapLibre's project() (Invariant 1: the client renders, never
// computes geography).
export const LEGEND_MIN_WIDTH_PX = 160;
export const LEGEND_VIEWPORT_MARGIN_PX = 24; // px kept clear on each side.

export function computeBboxScreenWidth(
  m: MapLibreMap,
  bbox: [number, number, number, number],
): number | null {
  // Width spans the bottom edge ([minLat]); the east-west corners are enough.
  const [minLon, minLat, maxLon] = bbox;
  let bl: { x: number; y: number };
  let br: { x: number; y: number };
  try {
    bl = m.project([minLon, minLat]);
    br = m.project([maxLon, minLat]);
  } catch {
    return null;
  }
  const raw = Math.abs(br.x - bl.x);
  if (!Number.isFinite(raw) || raw <= 0) return null;

  // Clamp: min so it stays legible; max so it can never overflow the viewport.
  let maxWidth = Number.POSITIVE_INFINITY;
  try {
    const c = m.getCanvas();
    if (c && c.clientWidth) maxWidth = c.clientWidth - LEGEND_VIEWPORT_MARGIN_PX * 2;
  } catch {
    maxWidth = Number.POSITIVE_INFINITY;
  }
  // Guard a degenerate (tiny) canvas so the max clamp can't drop below the min.
  if (maxWidth < LEGEND_MIN_WIDTH_PX) maxWidth = LEGEND_MIN_WIDTH_PX;
  return Math.max(LEGEND_MIN_WIDTH_PX, Math.min(raw, maxWidth));
}

// --- F74b feature-click/tap-to-inspect ---------------------------------- //
//
// The agent advertises "click polygons to see name / designation / IUCN", but
// until this feature nothing in the web client hit-tested rendered features.
// These helpers turn a hit feature's `properties` bag into popup-ready content.
// All of it is pure (Invariant 1: we surface received values verbatim — no
// geography is computed). The MapLibre wiring (queryRenderedFeatures, the
// click/touch handlers, the cursor) lives inside MapView below.

/**
 * Property keys (lowercased) we treat as the feature's NAME, in priority order.
 * Covers the WDPA live schema (`name_eng`), GBIF/iNat species fields, NWS
 * alerts, admin boundaries, OSM, and the generic `name`/`title` fallbacks.
 */
const NAME_KEYS: readonly string[] = [
  "name_eng",
  "name",
  "title",
  "orig_name",
  "site_name",
  "scientificname",
  "species",
  "vernacularname",
  "common_name",
  "event",
  "headline",
  "namelsad",
];

/**
 * Property keys (lowercased) we treat as the feature's DESIGNATION / TYPE, in
 * priority order. WDPA designation is `desig_eng`; others fall back to generic
 * type/category fields.
 */
const DESIGNATION_KEYS: readonly string[] = [
  "desig_eng",
  "designation",
  "desig",
  "type",
  "category",
  "feature_type",
  "highway",
  "landcover",
];

/** Property keys (lowercased) we treat as the IUCN category, in priority order. */
const IUCN_KEYS: readonly string[] = ["iucn_cat", "iucn_category", "iucn"];

/**
 * Keys we DROP from the generic attribute list because they are either internal
 * IDs / geometry noise or already surfaced as the title/subtitle/IUCN rows.
 */
const HIDDEN_ATTR_KEYS: ReadonlySet<string> = new Set([
  "geometry",
  "bbox",
  "id",
  "fid",
  "objectid",
  "shape_length",
  "shape_area",
  "shape__length",
  "shape__area",
]);

/** Humanize a raw property key for display: `name_eng` → "Name Eng", `iucn_cat` → "Iucn Cat". */
export function humanizePropertyKey(key: string): string {
  const cleaned = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim();
  if (!cleaned) return key;
  return cleaned
    .split(/\s+/)
    .map((w) => (w.length <= 1 ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

/** Coerce a property value to a compact display string, or null when not worth showing. */
export function stringifyPropertyValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") {
    const t = value.trim();
    return t.length > 0 ? t : null;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    // Trim long floats so coordinates / areas don't blow out the card.
    return Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  // Objects / arrays — JSON, but keep it short so the card stays compact.
  try {
    const s = JSON.stringify(value);
    if (!s || s === "{}" || s === "[]") return null;
    return s.length > 120 ? `${s.slice(0, 117)}…` : s;
  } catch {
    return null;
  }
}

/** Find the first present, non-empty value among `keys` (case-insensitive). */
function pickByKeys(
  props: Record<string, unknown>,
  lowerMap: Map<string, string>,
  keys: readonly string[],
): { key: string; value: string } | null {
  for (const k of keys) {
    const actual = lowerMap.get(k);
    if (actual === undefined) continue;
    const v = stringifyPropertyValue(props[actual]);
    if (v !== null) return { key: actual, value: v };
  }
  return null;
}

/**
 * Build the popup payload from a hit feature's properties + the originating
 * layer name + the screen point. `geomKindLabel` is a fallback title when the
 * feature has no name-like property (e.g. "Polygon"). Returns popup data with a
 * title, optional subtitle (designation/type/layer), IUCN row first when
 * present, then the remaining attributes (humanized, de-noised). Gracefully
 * handles a null/empty properties bag.
 */
export function buildFeaturePopupData(
  properties: Record<string, unknown> | null | undefined,
  point: { x: number; y: number },
  opts: { layerName?: string; geomKindLabel?: string } = {},
): FeaturePopupData {
  const props = properties ?? {};
  // Case-insensitive lookup map: lowercased key → original key.
  const lowerMap = new Map<string, string>();
  for (const k of Object.keys(props)) lowerMap.set(k.toLowerCase(), k);

  const nameHit = pickByKeys(props, lowerMap, NAME_KEYS);
  const desigHit = pickByKeys(props, lowerMap, DESIGNATION_KEYS);
  const iucnHit = pickByKeys(props, lowerMap, IUCN_KEYS);

  const usedKeys = new Set<string>();
  if (nameHit) usedKeys.add(nameHit.key.toLowerCase());
  if (desigHit) usedKeys.add(desigHit.key.toLowerCase());
  if (iucnHit) usedKeys.add(iucnHit.key.toLowerCase());

  const title = nameHit?.value ?? opts.layerName ?? opts.geomKindLabel ?? "Feature";
  // Subtitle prefers the designation; otherwise the layer name (when it wasn't
  // already used as the title).
  let subtitle: string | undefined;
  if (desigHit) subtitle = desigHit.value;
  else if (opts.layerName && opts.layerName !== title) subtitle = opts.layerName;

  const attributes: FeatureAttribute[] = [];
  // IUCN category leads the attribute list when present (advertised explicitly).
  if (iucnHit) attributes.push({ label: "IUCN Category", value: iucnHit.value });

  // Remaining properties — humanized, de-noised, in declaration order.
  for (const key of Object.keys(props)) {
    const lk = key.toLowerCase();
    if (usedKeys.has(lk)) continue;
    if (HIDDEN_ATTR_KEYS.has(lk)) continue;
    const v = stringifyPropertyValue(props[key]);
    if (v === null) continue;
    attributes.push({ label: humanizePropertyKey(key), value: v });
  }

  return { title, subtitle, attributes, point };
}

export function MapView({ subscribeSessionState, subscribeMapCommand, theme = "light" }: MapViewProps = {}): JSX.Element {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);
  // useRef so this survives effect re-runs without triggering re-render (A.7).
  const addedSourceIds = useRef<Set<string>>(new Set());
  // Per-layer geometry kind for added vector layers. Lets the update branch
  // pick the right paint property name (`circle-opacity` vs `line-opacity`
  // vs `fill-opacity`) when opacity/visibility changes on a known vector layer.
  // Also lets the visibility/opacity update path skip raster-only ops on vectors.
  const vectorGeomKinds = useRef<Map<string, VectorGeomKind>>(new Map());
  // job-0258: style_preset per layer_id, recorded when the layer is wired in.
  // The map-command opacity path needs it for the Pelicun fill multiplier,
  // and the command envelope itself doesn't carry presets.
  const layerStylePresets = useRef<Map<string, string | null>>(new Map());
  // Tracks the in-flight vector-fetch generation per layer_id. When a layer is
  // removed mid-fetch, this counter advances so a late-arriving fetch resolves
  // into a no-op rather than re-registering the source (kickoff §scope:
  // "Cleanup on remove: when a layer is removed... remove both source and
  // layer cleanly").
  const vectorFetchGen = useRef<Map<string, number>>(new Map());
  // AWS-migration hardening (bbox track): the last zoom-to bbox corners. The
  // analysis-extent rectangle and the camera move share one handler; if a
  // style (re)load happens AFTER the rectangle was drawn (theme setStyle,
  // per-Case MapView remount replay) the rectangle's source/layers are gone
  // with the old style. Remembering the corners lets a follow-up redraw
  // re-assert the rectangle without needing the bus to re-deliver the
  // command. Null until the first zoom-to. Kept inside this track's
  // ownership (no LayerPanel bus replay buffer — see crossTrackChanges).
  const lastZoomToCorners = useRef<[number, number, number, number] | null>(null);
  // ROOT-CAUSE FIX (job-0076 diagnosis): the prior implementation read
  // `payload.loaded_layers` synchronously in the subscriber and bailed if
  // `m.isStyleLoaded()` was false — so when session-state arrived BEFORE the
  // remote QGIS Server basemap tiles finished loading, the entire flood-layer
  // wiring was dropped on the floor with no retry. Diagnosis evidence:
  // `reports/inflight/job-0076-*/evidence/diagnosis.log` shows 69 basemap
  // tile responses + ZERO flood tile responses, and the post-injection style
  // spec contained only the basemap sources (no `flood-depth-job-0075-demo`
  // source/layer entries). Headline screenshots since job-0066 were
  // basemap-only because of this race.
  //
  // Fix: stash the latest session-state payload in a ref, and run an apply
  // function that (a) executes immediately if the style is ready, OR
  // (b) defers to the next `idle` / `load` event. The ref always carries
  // the latest payload, so multiple in-flight events collapse to the
  // most-recent state (still replace-not-reconcile per A.7).
  const latestSessionState = useRef<SessionStatePayload | null>(null);

  // job-0321 (F43) — the legend (depth-key / colorbar) now lives INSIDE the map
  // container so it can anchor to the AOI bounding box. Three pieces of state:
  //   1. legendLayers — the ordered ProjectLayerSummary list the legend needs,
  //      sourced from this component's own session-state subscription (App.tsx
  //      no longer mounts the legend, so it passes nothing). Ordered top-of-
  //      stack-first (z_index desc) to match LayerPanel + LayerLegend's
  //      `layers.find(...)` "topmost wins" contract.
  //   2. aoiBbox — the current AOI bbox corners (mirrors lastZoomToCorners into
  //      state so a re-render projects it). Null = no AOI → bottom-center.
  //   3. legendAnchor — the projected {left, top} bottom-edge midpoint of the
  //      AOI box, recomputed on map move/zoom/render (rAF-throttled). Null when
  //      there is no AOI or the box is off-screen → legend falls back to the
  //      previous bottom-center placement so it never disappears.
  const [legendLayers, setLegendLayers] = useState<ProjectLayerSummary[]>([]);
  const [aoiBbox, setAoiBbox] = useState<[number, number, number, number] | null>(null);
  const [legendAnchor, setLegendAnchor] = useState<LegendAnchor | null>(null);
  // FIX 4 (NATE 2026-06-17) — the AOI bbox's ON-SCREEN width in px, projected on
  // each map move/zoom (same listeners as legendAnchor). Null when there is no
  // AOI / the bbox is off-screen → LayerLegend uses its static 320 fallback.
  const [legendBarWidth, setLegendBarWidth] = useState<number | null>(null);
  const isMobile = useIsMobile();

  // F74b feature-click/tap-to-inspect. `featurePopup` is the currently-shown
  // popup payload (null = none). `mapCanvasSize` mirrors the canvas dimensions
  // so the popup can clamp itself on screen. The click/touch handler reads
  // `vectorGeomKinds` (which tracks every rendered vector layer_id) to build the
  // list of queryable layers, then queryRenderedFeatures hit-tests the point.
  const [featurePopup, setFeaturePopup] = useState<FeaturePopupData | null>(null);
  const [mapCanvasSize, setMapCanvasSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });
  // FIX 3 (NATE 2026-06-17) — the live map zoom, tracked so the popup can scale
  // with zoom (scale = 2^(zoom - refZoom), clamped). Updated on map move/zoom by
  // the popup-pin effect below. Null until the first projection.
  const [currentZoom, setCurrentZoom] = useState<number | null>(null);

  useEffect(() => {
    if (!container.current || map.current) return;
    const m = new maplibregl.Map({
      container: container.current,
      style: STYLE,
      center: CONUS_VIEW.center,
      zoom: CONUS_VIEW.zoom,
      maxPitch: 0,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      // job-0152: attribution tag removed for v0.1 demo (overlays other UI).
      // Users zoom via scroll/pinch/keyboard — no NavigationControl added below.
      // Production hosting should restore attribution per OSM tile-use terms.
      attributionControl: false,
    });
    // Decision I: 2D-only navigation. Belt + suspenders — explicitly disable
    // rotation in addition to constructor options so a future MapLibre default
    // change can't silently re-enable it.
    m.touchZoomRotate.disableRotation();
    m.keyboard.disableRotation();
    // job-0152: NavigationControl (zoom +/- and compass) removed — overlays
    // other UI elements. Scroll-zoom, pinch-zoom, and keyboard +/- remain
    // active (MapLibre defaults; no code change needed). See OQ below re: OSM
    // attribution terms.
    map.current = m;
    activeMap = m;
    // INCIDENT FIX 2026-06-16: latch style-readiness on the first `load` so a
    // later hung tile (which flips isStyleLoaded() back to false) can never
    // re-block layer adds/removals. See mapStyleReady().
    m.once("load", () => {
      (m as ReadyMap).__grace2StyleReady = true;
    });

    // Dev-only seam: expose the live MapLibre instance so the Playwright
    // diagnostic driver (reports/inflight/job-0076-*/evidence/) can introspect
    // m.getStyle() — i.e. confirm flood layer was added, capture the actual
    // tile URL template, etc. Production builds drop this via import.meta.env.
    if (import.meta.env.DEV) {
      (window as unknown as { __grace2GetMap?: () => MapLibreMap | null }).__grace2GetMap = () => map.current;
    }

    return () => {
      m.remove();
      map.current = null;
      if (activeMap === m) activeMap = null;
      if (import.meta.env.DEV) {
        delete (window as unknown as { __grace2GetMap?: () => MapLibreMap | null }).__grace2GetMap;
      }
    };
  }, []);

  // Subscribe to session-state and wire WMS raster sources (job-0068, change 4;
  // job-0076 race-condition fix). Replace-not-reconcile per A.7: diff
  // loaded_layers against addedSourceIds ref.
  // Invariant 4: QGIS Server renders all Tier B raster data; Map.tsx only
  // registers tile URLs — never computes colors, reads COGs, or touches GCS.
  useEffect(() => {
    if (!subscribeSessionState) return;

    /**
     * Apply the latest session-state payload (from `latestSessionState`) to
     * the live map. Idempotent — reads the ref each call so multiple deferred
     * calls collapse to the most-recent payload. Called both from the bus
     * subscription AND from the map "idle" handler in case the bus event
     * arrived before the style finished loading.
     */
    const applyLatest = () => {
      const m = map.current;
      const payload = latestSessionState.current;
      if (!m || !payload) return;
      // If the style isn't loaded yet, RE-ARM and retry on the next idle.
      // job-0258 live-probe finding: the previous `return` here did NOT
      // re-arm — the subscriber registers exactly one once("idle") per push,
      // and when that idle callback ran right after applyTheme had mutated
      // the style in the SAME idle dispatch (dark theme swaps the basemap),
      // isStyleLoaded() was false again and the whole layer batch was
      // silently dropped until the next session-state push. Re-arming makes
      // the deferral actually converge; applyLatest is idempotent
      // (replace-not-reconcile diff against addedSourceIds), so extra idle
      // invocations are harmless.
      // INCIDENT FIX 2026-06-16: gate on the mapStyleReady LATCH, not raw
      // isStyleLoaded(). A hung raster tile (vector-as-raster) keeps
      // isStyleLoaded() false forever — the old gate then deferred this whole
      // reconcile (adds AND removals AND the AOI) indefinitely, so the map
      // froze. Once the style has loaded once, proceed regardless of stuck
      // tiles; addSource/addLayer/removeLayer are all safe.
      if (!mapStyleReady(m)) {
        m.once("idle", applyLatest);
        return;
      }

      const currentLayers = payload.loaded_layers ?? [];
      const currentIds = new Set(currentLayers.map((l) => l.layer_id));

      // job-0357 (per-Case layer DURABILITY) — REMOVE only on an AUTHORITATIVE
      // replace. `replace_layers` is the client-only hint App.tsx stamps:
      //   - true / absent → full replace-not-reconcile (Case switch / exit, or
      //     a server snapshot received while the socket is healthy → live adds
      //     AND deletes apply). Absent defaults to true to preserve the
      //     historical behavior for older callers + unit fixtures.
      //   - false → additive reconcile: ADD/update layers in the snapshot but
      //     do NOT tear down tracked overlays absent from it. Set for server
      //     snapshots received while the socket is NOT `connected` (the
      //     disconnect / reconnect window) so a transient EMPTY or partial
      //     snapshot during a bare WS reconnect can never wipe the active
      //     Case's already-rendered layers (the bug this job fixes). The
      //     agent's resume replay carries the FULL persisted layer set, so on a
      //     healthy reconnect it lands as an idempotent no-op either way.
      const authoritativeReplace =
        (payload as { replace_layers?: boolean }).replace_layers !== false;

      // Remove layers that are gone (replace-not-reconcile).
      //
      // F84 ROOT-CAUSE FIX: a session-state replace (Case switch / Case exit
      // with loaded_layers:[]) MUST drop EVERY currently-rendered overlay whose
      // layer_id is not in the new set — raster AND inline-GeoJSON vector. The
      // prior code only removed the single MapLibre layer named `id` plus its
      // source. That is correct for rasters (one MapLibre layer per source) but
      // WRONG for vectors: `registerVectorOnMap` adds SEVERAL MapLibre layers
      // per geojson source —
      //     polygon:      `${id}` (fill) + `${id}-outline` (line)
      //     dense point:  `${id}-clusters` + `${id}-cluster-count` + `${id}`
      // so removing only `${id}` left e.g. the `${id}-outline` layer still
      // referencing the source. MapLibre then THROWS on removeSource(id)
      // ("Source can't be removed while layer is using it"), and because that
      // throw was uncaught it aborted the whole removal loop — so WDPA-style
      // polygon vectors persisted across Case switches / Case exit (the bug).
      //
      // Fix: remove EVERY member of the layer group (via layerGroupMemberIds,
      // the same bottom-to-top member list registerVectorOnMap built) BEFORE
      // removing the source, each guarded so one bad call can't abort the loop.
      // An empty currentIds (loaded_layers:[]) => every tracked overlay is gone
      // => all overlays removed (fresh slate). Basemap layers are never tracked
      // in addedSourceIds, so they are untouched.
      //
      // job-0357: this teardown is SKIPPED entirely on a non-authoritative
      // (additive) reconcile — a reconnect top-up never removes durable layers.
      // The ADD/update loop below always runs, so an additive snapshot still
      // registers any newly-rendered layer it carries.
      if (authoritativeReplace) {
      // Fresh slate on Case switch / authoritative replace: clear any lingering
      // feature highlight + inspect popup so they never carry across Cases
      // (job-0357 must-fix). A bare reconnect is replace_layers===false and does
      // NOT reach here, so durable layers (and a highlight) survive a reconnect.
      try {
        if (m.getSource(FEATURE_HIGHLIGHT_SOURCE_ID)) clearFeatureHighlight(m);
      } catch {
        /* highlight already gone — best-effort */
      }
      setFeaturePopup(null);
      for (const id of addedSourceIds.current) {
        if (!currentIds.has(id)) {
          // Remove all MapLibre paint layers belonging to this logical layer
          // (fill + outline, or cluster + cluster-count + points, or the lone
          // raster/point/line layer) so the source is no longer referenced.
          for (const member of layerGroupMemberIds(m, id)) {
            try {
              m.removeLayer(member);
            } catch {
              // Mid-removal race / already gone — keep going so a single bad
              // member can't leave the rest (or the source) behind.
            }
          }
          try {
            if (m.getSource(id)) m.removeSource(id);
          } catch {
            // Source still referenced (a member we couldn't remove) or already
            // gone — best-effort; the next reconcile re-attempts.
          }
          addedSourceIds.current.delete(id);
          // job-0139: tear down vector bookkeeping too. Bump fetch generation
          // so any in-flight fetch for this layer_id resolves into a no-op.
          vectorGeomKinds.current.delete(id);
          layerStylePresets.current.delete(id);
          vectorFetchGen.current.set(id, (vectorFetchGen.current.get(id) ?? 0) + 1);
        }
      }
      }

      // Add new layers; update opacity/visibility on existing.
      // Cast to WireLayerSummary: the agent emits `uri` on the wire even though
      // contracts.ts uses `source_url` (schema mismatch; tracked as OQ-0068-URI).
      for (const _layer of currentLayers) {
        const layer = _layer as unknown as WireLayerSummary;
        const opacity = layer.opacity ?? 1;
        const visible = layer.visible !== false;
        const layerType = layer.layer_type;
        // job-0258: keep the preset bookkeeping current for the map-command
        // opacity path (Pelicun fill multiplier).
        layerStylePresets.current.set(layer.layer_id, layer.style_preset ?? null);

        if (addedSourceIds.current.has(layer.layer_id)) {
          // Update paint/layout on existing layer via the shared helpers
          // (job-0258) — these branch on the tracked geometry kind AND cover
          // sublayers (`-outline`, `-clusters`, `-cluster-count`) that the
          // previous inline branch missed.
          if (m.getLayer(layer.layer_id)) {
            const geomKind = vectorGeomKinds.current.get(layer.layer_id);
            applyLayerOpacity(m, layer.layer_id, opacity, geomKind, layer.style_preset);
            applyLayerVisibility(m, layer.layer_id, visible);
          }
          continue;
        }

        // New layer — branch on layer_type.
        if (
          (layerType === "vector" || layerType === "geojson") &&
          typeof layer.vector_tile_url === "string" &&
          layer.vector_tile_url.length > 0
        ) {
          // F94: DENSE vector path. The agent published a vector-tile source
          // (MVT / PMTiles) instead of inline GeoJSON, so MapLibre fetches +
          // draws only the tiles in view. Synchronous register (no async
          // fetch); guard on style-ready the same way the inline path does.
          addedSourceIds.current.add(layer.layer_id);
          let styleReady = false;
          try {
            styleReady = mapStyleReady(m);
          } catch {
            styleReady = false;
          }
          if (styleReady) {
            registerVectorTileLayer(m, layer as unknown as Parameters<typeof registerVectorTileLayer>[1], vectorGeomKinds);
          } else {
            // Defer until the style settles (mirrors addVectorLayer's retry).
            m.once("idle", () => {
              if (!addedSourceIds.current.has(layer.layer_id)) return;
              try {
                if (!m.isStyleLoaded()) return;
              } catch {
                return;
              }
              registerVectorTileLayer(m, layer as unknown as Parameters<typeof registerVectorTileLayer>[1], vectorGeomKinds);
            });
          }
        } else if (layerType === "vector" || layerType === "geojson") {
          // job-0139: vector layer path. Fetch GeoJSON/FlatGeobuf, add a
          // GeoJSON source, paint per geometry kind.
          //
          // We mark the slot reserved (addedSourceIds.add) BEFORE the async
          // fetch resolves so a second session-state push during the fetch
          // doesn't double-register. The fetch generation counter guards
          // against the re-add race where a layer is removed + re-added
          // before the original fetch resolves.
          addedSourceIds.current.add(layer.layer_id);
          const gen = (vectorFetchGen.current.get(layer.layer_id) ?? 0) + 1;
          vectorFetchGen.current.set(layer.layer_id, gen);
          void addVectorLayer(m, layer, gen, vectorFetchGen, vectorGeomKinds, addedSourceIds);
        } else {
          // Raster (existing path).
          //
          // MapLibre paints layers in insertion order; we don't pass an
          // explicit beforeId here because the basemap was added first via
          // the seed style spec, so any flood layer added now will paint
          // ABOVE it (correct stacking). The dark-theme swap path
          // (`applyTheme` below) preserves this invariant by re-adding the
          // basemap with `beforeId =` first overlay layer, so overlays
          // always stay on top of whichever basemap is active.
          // job-0171: pass style_preset so the LAYERS= shim can recover the
          // missing parameter for tools that emit only the bare WMS endpoint
          // (e.g. fetch_nexrad_reflectivity). See OQ-0171-WMS-URL-CONTRACT.
          const tileUrl = buildWmsTileUrl(layer.uri, layer.style_preset ?? null);
          m.addSource(layer.layer_id, {
            type: "raster",
            tiles: [tileUrl],
            tileSize: 256,
          });
          // raster-resampling: nearest preserves discrete COG cell boundaries
          // (job-0078 diagnosis). Without this, MapLibre's default `linear`
          // bilinear interpolation smears flood-depth cells across screen pixels,
          // making it impossible to visually verify per-cell alignment with
          // underlying basemap features (streets, building blocks). nearest
          // shows the source-projection grid 1:1 — the user can see that each
          // flood cell sits over the specific street/lot it covers, which is
          // the only visually-irrefutable proof of geographic alignment.
          m.addLayer({
            id: layer.layer_id,
            type: "raster",
            source: layer.layer_id,
            paint: {
              "raster-opacity": opacity,
              "raster-resampling": "nearest",
            },
            layout: { visibility: visible ? "visible" : "none" },
          });
          addedSourceIds.current.add(layer.layer_id);
        }
      }
    };

    const unsub = subscribeSessionState((payload) => {
      latestSessionState.current = payload;

      // job-0321 (F43) — capture the ordered layer list the legend needs from
      // THIS component's own subscription (App.tsx no longer owns the legend).
      // Order top-of-stack-first (z_index DESCENDING) to match LayerPanel and
      // LayerLegend's `layers.find(...)` "topmost wins" contract. The wire
      // payload carries z_index when present; when it's absent on every layer
      // (older snapshots) the sort is stable and preserves emission order,
      // which is already roughly top-of-stack-last → we reverse in that case so
      // the most-recently-added (topmost) layer is first. We detect "no usable
      // z_index" as: every layer's z_index is undefined.
      const raw = (payload.loaded_layers ?? []) as ProjectLayerSummary[];
      const anyZ = raw.some(
        (l) => typeof (l as { z_index?: unknown }).z_index === "number",
      );
      const ordered = anyZ
        ? [...raw].sort((a, b) => (b.z_index ?? 0) - (a.z_index ?? 0))
        : [...raw].reverse();
      setLegendLayers(ordered);

      const m = map.current;
      if (!m) return;
      if (m.isStyleLoaded()) {
        applyLatest();
      }
      // Whether or not we applied synchronously, attach an idle handler so
      // any subsequent style-load completes the reconciliation. `idle` fires
      // once per loop-tick when all in-flight requests settle.
      m.once("idle", applyLatest);
    });
    return unsub;
  }, [subscribeSessionState]);

  // Subscribe to theme prop changes and swap the basemap source+layer
  // (job-0076 bundled enhancement). The swap pattern:
  //   1. Pick the lowest-priority existing flood-overlay layer as the
  //      beforeId target so the new basemap renders UNDER everything else.
  //   2. Remove the current basemap layer + source.
  //   3. Add the new basemap source + layer, passing beforeId so MapLibre
  //      inserts it underneath the flood overlays.
  // Order-preservation note: flood overlays were added via addLayer with no
  // beforeId, so they live at the TOP of the layer stack. Re-inserting the
  // basemap underneath them keeps the same painter's-algorithm order.
  useEffect(() => {
    const m = map.current;
    if (!m) return;

    const applyTheme = () => {
      const currentMap = map.current;
      if (!currentMap || !currentMap.isStyleLoaded()) {
        // Defer until style is ready.
        currentMap?.once("idle", applyTheme);
        return;
      }

      const style = currentMap.getStyle();
      const layerIds = style.layers.map((l) => l.id);

      // The lowest flood-overlay layer (i.e. the first one we added beyond the
      // basemap layers) is our `beforeId` target — the new basemap layer
      // should be inserted just before it. If no flood overlays exist yet,
      // append; the basemap will be the top layer until a flood overlay is
      // added, at which point the flood overlay will paint above it (correct).
      const firstFloodLayer = layerIds.find(
        (id) => id !== BASEMAP_LAYER_ID && id !== DARK_BASEMAP_LAYER_ID && id !== "osm-fallback-basemap",
      );

      if (theme === "dark") {
        // Remove light basemap layer+source if present.
        if (currentMap.getLayer(BASEMAP_LAYER_ID)) currentMap.removeLayer(BASEMAP_LAYER_ID);
        // (Leave the qgis-wms source in place — removing it can race with
        // any pending tile requests; harmless to keep since it has no layer
        // referencing it.)
        // Add dark basemap if not already there.
        if (!currentMap.getSource(DARK_BASEMAP_SOURCE_ID)) {
          currentMap.addSource(DARK_BASEMAP_SOURCE_ID, {
            type: "raster",
            tiles: [CARTO_DARK_TILE_TEMPLATE],
            tileSize: 256,
            attribution: CARTO_DARK_ATTRIBUTION,
            maxzoom: 19,
          });
        }
        if (!currentMap.getLayer(DARK_BASEMAP_LAYER_ID)) {
          currentMap.addLayer(
            {
              id: DARK_BASEMAP_LAYER_ID,
              type: "raster",
              source: DARK_BASEMAP_SOURCE_ID,
              minzoom: 0,
              maxzoom: 22,
            },
            firstFloodLayer,
          );
        }
      } else {
        // light theme — restore QGIS WMS basemap.
        if (currentMap.getLayer(DARK_BASEMAP_LAYER_ID)) currentMap.removeLayer(DARK_BASEMAP_LAYER_ID);
        if (!currentMap.getLayer(BASEMAP_LAYER_ID)) {
          // Source was kept; just re-add the layer.
          currentMap.addLayer(
            {
              id: BASEMAP_LAYER_ID,
              type: "raster",
              source: BASEMAP_SOURCE_ID,
              minzoom: 0,
              maxzoom: 22,
            },
            firstFloodLayer,
          );
        }
      }

      // AWS-migration hardening (bbox track): a theme swap mutates the style;
      // if the analysis-extent rectangle was ever drawn, re-assert it so a
      // future setStyle-based theme path (or any style churn that dropped it)
      // self-heals. drawAnalysisExtent is idempotent + missing-layer-healing,
      // so this is a no-op when the extent is already intact.
      if (lastZoomToCorners.current) {
        try {
          drawAnalysisExtent(currentMap, lastZoomToCorners.current);
        } catch (err) {
          if (import.meta.env.DEV) {
            // eslint-disable-next-line no-console
            console.warn("[MapView] extent redraw on theme change threw:", err);
          }
        }
      }
    };

    applyTheme();
    // No cleanup — basemap state lives in the map's style; the next theme
    // change will reconcile it.
  }, [theme]);

  // Subscribe to map-command for zoom-to and transient camera/animation verbs
  // (job-0068, change 5 client side) PLUS the layer-control verbs
  // set-layer-opacity / set-layer-visibility / set-layer-order (job-0258 —
  // the LayerPanel user controls emit these through the App bus; until this
  // handler existed they never reached the map, which is why the panel's
  // opacity slider and drag-reorder were dead in the live demo). Layer CRUD
  // (load-layer / remove-layer) stays DEFERRED to the session-state path per
  // layer-emission-contract.md.
  // WireMapCommand extends frozen contracts.ts MapCommandPayload with zoom-to
  // (which is deferred in contracts.ts but needed here per kickoff).
  useEffect(() => {
    if (!subscribeMapCommand) return undefined;
    return subscribeMapCommand((payload: WireMapCommand) => {
      const m = map.current;
      if (!m) return;
      if (payload.command === "zoom-to") {
        const { bbox } = (payload as ZoomToCommand).args;
        if (bbox && bbox.length === 4) {
          const corners = bbox as [number, number, number, number];
          const [minLon, minLat, maxLon, maxLat] = corners;
          // Respect prefers-reduced-motion: a 1200ms camera flight is motion.
          // When the user has asked for reduced motion, jump (duration 0) so
          // there is no animation — the moveend below still fires synchronously
          // enough that the extent redraw lands without an animated sweep.
          const prefersReducedMotion =
            typeof window !== "undefined" &&
            typeof window.matchMedia === "function" &&
            window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          m.fitBounds(
            [[minLon, minLat], [maxLon, maxLat]],
            { padding: 40, duration: prefersReducedMotion ? 0 : 1200 },
          );
          // Remember the last extent corners so a late style (re)load (theme
          // setStyle, case-reopen remount) can re-assert the rectangle. Stays
          // inside this track's ownership (a ref, not the cross-track bus
          // replay buffer — see crossTrackChanges).
          lastZoomToCorners.current = corners;
          // job-0321 (F43) — mirror the corners into state so the legend can
          // re-project against the AOI box (the legend hangs off its bottom
          // edge). The projection effect recomputes legendAnchor whenever aoiBbox
          // or the camera changes.
          setAoiBbox(corners);
          // job-0294 — ALSO outline the extent as a styled rectangle so the
          // user sees exactly what area is being measured. The fitBounds above
          // is camera-only; this draws the bbox on the map.
          //
          // AWS-migration root-cause fix (bbox track): the prior code wrapped
          // drawAnalysisExtent in a bare `catch {}` that SILENTLY SWALLOWED any
          // throw — so a transient MapLibre "style not done loading" /
          // source-not-ready / mid-camera-animation style-churn throw dropped
          // the rectangle forever (camera moved, no rectangle: the live
          // symptom). Now a throw RE-SCHEDULES the draw on the next idle, with
          // a small bounded retry counter so a persistently-broken style (dead
          // basemap WMS post-migration) can't loop unbounded. We also defer
          // while the style is not loaded (case-reopen replay can race the
          // first style load) AND re-assert AFTER the camera flight settles
          // (moveend) to cover the window where the raster/vector source add
          // churns the style mid-animation. drawAnalysisExtent is idempotent
          // and self-healing, so every extra invocation is harmless.
          let retries = 0;
          const MAX_RETRIES = 3;
          const drawExtent = (): void => {
            if (!map.current) return;
            // INCIDENT FIX 2026-06-16: a Case-exit clear-analysis-extent sets
            // lastZoomToCorners=null. If a redraw was already queued (the
            // moveend/idle re-assert below), it must NOT re-add the rectangle
            // after the clear — otherwise the AOI box persists after leaving the
            // Case (user-reported). Bail when the corners were cleared.
            if (lastZoomToCorners.current === null) return;
            // INCIDENT FIX 2026-06-16: gate the AOI draw on the mapStyleReady
            // LATCH, not raw isStyleLoaded() — a hung raster tile keeps
            // isStyleLoaded() false forever and would stall the bounding-box
            // draw indefinitely (the "no bounding box" symptom). Once the style
            // has loaded once, draw regardless of stuck tiles.
            if (!mapStyleReady(map.current)) {
              map.current.once("idle", drawExtent);
              return;
            }
            try {
              drawAnalysisExtent(map.current, corners);
            } catch (err) {
              // Mid style-mutation race; re-schedule rather than drop. Bounded
              // so a permanently-broken style cannot loop forever.
              if (import.meta.env.DEV) {
                // eslint-disable-next-line no-console
                console.warn(
                  `[MapView] drawAnalysisExtent threw (retry ${retries + 1}/${MAX_RETRIES}):`,
                  err,
                );
              }
              if (retries < MAX_RETRIES && map.current) {
                retries += 1;
                map.current.once("idle", drawExtent);
              }
            }
          };
          drawExtent();
          // Re-assert AFTER the camera flight settles. The agent emits
          // session-state (raster/vector source add) BEFORE this zoom-to, but
          // the animated fitBounds keeps mutating the style for ~1200ms; a
          // redraw on moveend lands the dashed outline once the style is quiet.
          // Idempotent: drawAnalysisExtent setData-replaces + heals missing
          // layers, so this never double-adds.
          m.once("moveend", drawExtent);
        }
      } else if (payload.command === "clear-analysis-extent") {
        // ux-batch-1 (F14): Case exit (or opening a Case with no AOI) must not
        // leave the prior Case's analysis-extent rectangle on the map. Forget
        // the remembered corners FIRST so a late style (re)load can't re-assert
        // the rectangle via the moveend/idle redraw path, then remove it.
        lastZoomToCorners.current = null;
        // job-0321 (F43) — drop the AOI bbox so the legend falls back to its
        // bottom-center placement (no AOI to anchor to anymore).
        setAoiBbox(null);
        // INCIDENT FIX 2026-06-16: gate on mapStyleReady, not raw
        // isStyleLoaded(). A hung tile (or a mid-flight camera animation) kept
        // isStyleLoaded() false, so the clear deferred forever and the AOI
        // rectangle PERSISTED after leaving a Case (user-reported). Once the
        // style has loaded once, removeLayer/removeSource are safe — clear now.
        if (mapStyleReady(m)) {
          try {
            clearAnalysisExtent(m);
          } catch {
            // Mid style-mutation race; the extent is removed best-effort. A
            // missing/half-built extent is harmless and self-heals on next draw.
          }
        } else {
          m.once("idle", () => {
            if (!map.current) return;
            try {
              clearAnalysisExtent(map.current);
            } catch {
              /* best-effort — see above */
            }
          });
        }
      } else if (payload.command === "reset-view") {
        // ux-batch-1 (F-CASES-CLEAR-ALL): leaving a Case snaps the camera back
        // to the default CONUS view so the user clearly sees they are no longer
        // in a Case. Camera-only — the extent rectangle is cleared separately
        // by the clear-analysis-extent command App also emits on exit.
        // job-0321 (F43) — also drop the AOI bbox so the legend stops trying to
        // anchor to a box that is no longer on screen (belt + suspenders with
        // the clear-analysis-extent command).
        setAoiBbox(null);
        const prefersReducedMotion =
          typeof window !== "undefined" &&
          typeof window.matchMedia === "function" &&
          window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        m.flyTo({
          center: CONUS_VIEW.center,
          zoom: CONUS_VIEW.zoom,
          duration: prefersReducedMotion ? 0 : 800,
        });
      } else if (payload.command === "set-layer-opacity") {
        const opacity = Math.max(0, Math.min(1, payload.opacity));
        applyLayerOpacity(
          m,
          payload.layer_id,
          opacity,
          vectorGeomKinds.current.get(payload.layer_id),
          layerStylePresets.current.get(payload.layer_id) ?? null,
        );
      } else if (payload.command === "set-layer-visibility") {
        applyLayerVisibility(m, payload.layer_id, payload.visible);
      } else if (payload.command === "set-layer-order") {
        applyLayerOrder(m, payload.layer_ids);
      } else {
        // eslint-disable-next-line no-console
        console.warn("[MapView] MapCommand not yet implemented:", payload.command);
      }
    });
  }, [subscribeMapCommand]);

  // job-0321 (F43) — keep the legend anchored to the AOI box's bottom edge as
  // the camera moves. Re-project the bbox bottom-edge midpoint on every map
  // `move` / `zoom` / `render` (render fires throughout the fitBounds flight),
  // throttled to one update per animation frame so a 60fps pan doesn't thrash
  // setState. When aoiBbox is null (AOI-less Case / after Case-exit) the anchor
  // is cleared so the legend reverts to bottom-center. Listeners are cleaned up
  // on unmount / when aoiBbox changes.
  useEffect(() => {
    const m = map.current;
    if (!m) return undefined;

    if (!aoiBbox) {
      setLegendAnchor(null);
      // FIX 4 — no AOI bbox → drop the projected width so the legend reverts to
      // its static 320 fallback.
      setLegendBarWidth(null);
      return undefined;
    }

    let rafId: number | null = null;
    let disposed = false;
    const recompute = () => {
      rafId = null;
      if (disposed) return;
      const cur = map.current;
      if (!cur) return;
      setLegendAnchor(computeBboxBottomAnchor(cur, aoiBbox));
      // FIX 4 — project the bbox's on-screen east-west width on the SAME
      // move/zoom listeners, so the colorbar spans the box + shrinks on zoom-out.
      setLegendBarWidth(computeBboxScreenWidth(cur, aoiBbox));
    };
    const schedule = () => {
      if (rafId != null) return; // already queued this frame
      if (typeof requestAnimationFrame === "function") {
        rafId = requestAnimationFrame(recompute);
      } else {
        // SSR / test environments without rAF — compute synchronously.
        recompute();
      }
    };

    // Initial projection + on every camera change.
    schedule();
    m.on("move", schedule);
    m.on("zoom", schedule);
    m.on("render", schedule);

    return () => {
      disposed = true;
      if (rafId != null && typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(rafId);
      }
      try {
        m.off("move", schedule);
        m.off("zoom", schedule);
        m.off("render", schedule);
      } catch {
        /* map may already be torn down */
      }
    };
  }, [aoiBbox]);

  // F74b feature-click/tap-to-inspect. The agent advertises "click polygons to
  // see name / designation / IUCN", so a click OR a tap on a rendered vector
  // feature must surface its attributes. Mechanism:
  //   - On `click` (fires for mouse AND for a tap on touch devices — MapLibre
  //     synthesizes a click from a tap that did not pan), run
  //     queryRenderedFeatures at the point restricted to the rendered vector
  //     paint layers (tracked in vectorGeomKinds — every layer_id we painted as
  //     a circle/line/fill). On a hit, open the popup at the point.
  //   - Desktop hover: set the canvas cursor to "pointer" over a hittable
  //     feature (mouseenter/mouseleave per layer) so users know it's clickable.
  //   - Dismiss: tapping the empty map (no hit) closes any open popup; the X
  //     button and Esc are handled inside FeaturePopup.
  // queryRenderedFeatures is the MapLibre hit-test — Invariant 1 holds: every
  // value shown comes from the feature's own properties, nothing is computed.
  useEffect(() => {
    const m = map.current;
    if (!m) return undefined;

    // Geometry-kind → human label for the no-name title fallback.
    const geomLabel = (kind: VectorGeomKind | undefined): string => {
      switch (kind) {
        case "point":
          return "Point";
        case "line":
          return "Line";
        case "polygon":
          return "Polygon";
        default:
          return "Feature";
      }
    };

    // The set of MapLibre layer ids we hit-test: the main paint layer for every
    // tracked vector layer_id that currently exists on the map. (Cluster
    // sublayers / polygon outlines are intentionally excluded — the main paint
    // layer carries the real per-feature properties.)
    const queryableLayerIds = (): string[] => {
      const ids: string[] = [];
      for (const id of vectorGeomKinds.current.keys()) {
        try {
          if (m.getLayer(id)) ids.push(id);
        } catch {
          /* layer mid-removal — skip */
        }
      }
      return ids;
    };

    const readCanvasSize = (): { width: number; height: number } => {
      try {
        const c = m.getCanvas();
        if (c) return { width: c.clientWidth, height: c.clientHeight };
      } catch {
        /* fall through */
      }
      return { width: 0, height: 0 };
    };

    const onMapClick = (e: maplibregl.MapMouseEvent): void => {
      const layers = queryableLayerIds();
      if (layers.length === 0) {
        setFeaturePopup(null);
        // FIX 1 — no vector layers → nothing to highlight; clear any stale one.
        try {
          if (m.getSource(FEATURE_HIGHLIGHT_SOURCE_ID)) clearFeatureHighlight(m);
        } catch {
          /* best effort */
        }
        return;
      }
      let features: maplibregl.MapGeoJSONFeature[] = [];
      try {
        features = m.queryRenderedFeatures(e.point, { layers });
      } catch {
        features = [];
      }
      if (!features || features.length === 0) {
        // Tap on empty map (or basemap/raster) dismisses any open popup AND
        // clears the highlight (FIX 1 — a no-hit tap replaces/clears it).
        setFeaturePopup(null);
        try {
          if (m.getSource(FEATURE_HIGHLIGHT_SOURCE_ID)) clearFeatureHighlight(m);
        } catch {
          /* best effort */
        }
        return;
      }
      const hit = features[0]!;
      const sourceId =
        typeof (hit as { layer?: { source?: unknown } }).layer?.source === "string"
          ? ((hit as unknown as { layer: { source: string } }).layer.source as string)
          : undefined;
      const geomKind = sourceId ? vectorGeomKinds.current.get(sourceId) : undefined;
      // FIX 1 — highlight the ENTIRE tapped feature geometry. Generic across
      // polygon/line/point: setFeatureHighlight feeds the cloned geometry into a
      // single highlight source whose fill/line/circle layers paint whichever
      // kind matches. Map-space, so it pans + scales with zoom for free. Replaces
      // any prior highlight (a new tap = a new highlight).
      try {
        setFeatureHighlight(m, (hit.geometry ?? null) as Geometry | null);
      } catch {
        /* highlight is best-effort; the popup still opens */
      }
      // FIX 2 — capture the feature's geographic anchor so the popup stays glued
      // to its MAP location across pans/zooms. FIX 3 — capture the zoom at tap as
      // the scale reference. e.lngLat is MapLibre's geographic coordinate of the
      // tap point (Invariant 1: received from MapLibre, not computed).
      const lngLat =
        e.lngLat && typeof e.lngLat.lng === "number" && typeof e.lngLat.lat === "number"
          ? { lng: e.lngLat.lng, lat: e.lngLat.lat }
          : undefined;
      let refZoom: number | undefined;
      try {
        refZoom = m.getZoom();
      } catch {
        refZoom = undefined;
      }
      const data: FeaturePopupData = {
        ...buildFeaturePopupData(
          (hit.properties ?? null) as Record<string, unknown> | null,
          { x: e.point.x, y: e.point.y },
          { layerName: sourceId, geomKindLabel: geomLabel(geomKind) },
        ),
        lngLat,
        refZoom,
      };
      setMapCanvasSize(readCanvasSize());
      if (typeof refZoom === "number") setCurrentZoom(refZoom);
      setFeaturePopup(data);
    };

    // Desktop cursor affordance — pointer over a hittable feature. We attach a
    // single mousemove handler (cheap) instead of per-layer enter/leave so it
    // keeps working as vector layers come and go without re-binding.
    const onMouseMove = (e: maplibregl.MapMouseEvent): void => {
      const layers = queryableLayerIds();
      if (layers.length === 0) {
        m.getCanvas().style.cursor = "";
        return;
      }
      let features: maplibregl.MapGeoJSONFeature[] = [];
      try {
        features = m.queryRenderedFeatures(e.point, { layers });
      } catch {
        features = [];
      }
      m.getCanvas().style.cursor = features && features.length > 0 ? "pointer" : "";
    };

    m.on("click", onMapClick);
    m.on("mousemove", onMouseMove);

    return () => {
      try {
        m.off("click", onMapClick);
        m.off("mousemove", onMouseMove);
      } catch {
        /* map may already be torn down */
      }
    };
  }, []);

  // FIX 2 + FIX 3 (NATE 2026-06-17) — keep the popup PINNED TO THE MAP and
  // TRACK ZOOM for the scale transform. While a popup with a geographic anchor
  // (`lngLat`) is open, re-project that lng/lat to a screen point on every map
  // `move` / `zoom` so the card stays glued to the feature's MAP location (pans
  // with the map, same spot on the map), and mirror the live map zoom into
  // `currentZoom` so the card scales like a map-drawn label. rAF-throttled so a
  // 60fps pan doesn't thrash setState. Re-projection updates only `point` so the
  // popup content / lngLat / refZoom are preserved. Re-armed whenever the popup
  // identity (its lngLat) changes; torn down when the popup closes.
  const popupLng = featurePopup?.lngLat?.lng;
  const popupLat = featurePopup?.lngLat?.lat;
  useEffect(() => {
    const m = map.current;
    if (!m) return undefined;
    if (typeof popupLng !== "number" || typeof popupLat !== "number") {
      return undefined; // no geographic anchor (older fixtures) → stays screen-anchored.
    }

    let rafId: number | null = null;
    let disposed = false;
    const recompute = () => {
      rafId = null;
      if (disposed) return;
      const cur = map.current;
      if (!cur) return;
      let pt: { x: number; y: number };
      try {
        pt = cur.project([popupLng, popupLat]);
      } catch {
        return;
      }
      let z: number;
      try {
        z = cur.getZoom();
      } catch {
        z = currentZoom ?? 0;
      }
      setCurrentZoom(z);
      // Update only `point` — keep the rest of the popup payload intact so the
      // card re-renders glued to the feature's projected map location.
      setFeaturePopup((prev) =>
        prev ? { ...prev, point: { x: pt.x, y: pt.y } } : prev,
      );
    };
    const schedule = () => {
      if (rafId != null) return;
      if (typeof requestAnimationFrame === "function") {
        rafId = requestAnimationFrame(recompute);
      } else {
        recompute(); // SSR / test env without rAF → synchronous.
      }
    };

    // Project once now (so the card lands on the anchor immediately), then on
    // every camera change.
    schedule();
    m.on("move", schedule);
    m.on("zoom", schedule);

    return () => {
      disposed = true;
      if (rafId != null && typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(rafId);
      }
      try {
        m.off("move", schedule);
        m.off("zoom", schedule);
      } catch {
        /* map may already be torn down */
      }
    };
    // currentZoom is intentionally omitted — it's a fallback read, not a trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [popupLng, popupLat]);

  // --- Region-disambiguation choropleth sync (state-bbox-fallback) -------- //
  //
  // Subscribe to the region-choice bus so the candidate county choropleth stays
  // in lockstep with the in-chat RegionPickerCard list:
  //   - A new request draws the choropleth + zooms to the whole-state bbox so
  //     the candidate counties are in view; a cleared request (the user
  //     answered) tears it down. Replace-on-new-request (Appendix A.7).
  //   - hovered/selected ids drive each polygon's feature-state highlight so a
  //     CARD-ROW hover/click highlights the matching polygon.
  //   - A polygon TAP (or hover) feeds the bus (pickRegion / setHovered) so the
  //     map drives the card the same way the card drives the map — one reply
  //     path (Chat owns the WebSocket; the bus relays the map tap to it).
  //
  // All MapLibre source/layer work is gated on style-readiness + existence
  // guards (mirrors the analysis-extent + vector-layer paths). Invariant 4: the
  // client only registers sources/layers; Invariant 1: geometry is verbatim
  // from the received candidate bboxes.
  useEffect(() => {
    const highlightIds = { current: new Set<string>() };
    let currentRequestId: string | null = null;

    // Apply a bus snapshot to the map. Idempotent; safe to call before the
    // style is ready (it re-arms on the next idle via the map "idle" handler
    // registered below).
    const apply = (st: RegionChoiceBusState): void => {
      const m = map.current;
      if (!m) return;
      if (!mapStyleReady(m)) return;
      const req = st.request;
      if (!req) {
        // Cleared — tear down the choropleth.
        if (currentRequestId !== null) {
          clearRegionChoropleth(m);
          highlightIds.current = new Set();
          currentRequestId = null;
        }
        return;
      }
      // New request (or first paint) — draw + frame the whole-state bbox so the
      // candidate counties are visible.
      if (currentRequestId !== req.request_id) {
        drawRegionChoropleth(m, req.candidates);
        currentRequestId = req.request_id;
        highlightIds.current = new Set();
        try {
          const [minLon, minLat, maxLon, maxLat] = req.state_bbox;
          m.fitBounds(
            [
              [minLon, minLat],
              [maxLon, maxLat],
            ],
            { padding: 48, duration: 600, maxZoom: 8 },
          );
        } catch {
          /* fitBounds can throw on a degenerate bbox — leave the camera */
        }
      } else {
        // Same request — keep the data fresh (candidates are stable, but a
        // re-emit is harmless) without re-framing the camera.
        drawRegionChoropleth(m, req.candidates);
      }
      highlightIds.current = applyRegionChoiceHighlight(
        m,
        st.hoveredRegionId,
        st.selectedRegionId,
        highlightIds.current,
      );
    };

    // The bus fires immediately on subscribe with the current state, so a Map
    // mounting AFTER the request arrived paints the choropleth right away.
    const unsub = regionChoiceBus.subscribe((st) => {
      const m = map.current;
      // Nothing to draw / tear down (no active request and none currently
      // painted) → do not arm an idle deferral. This keeps the common no-pick
      // case (the vast majority of sessions) from touching the map at all.
      if (!st.request && currentRequestId === null) return;
      if (m && !mapStyleReady(m)) {
        // Defer until the style is ready; re-read the live bus state then so we
        // don't paint a stale snapshot.
        m.once("idle", () => apply(regionChoiceBus.getState()));
        return;
      }
      apply(st);
    });

    // Map TAP on a candidate polygon → relay a pick to the bus (Chat sends the
    // reply). A hover over a polygon highlights it via the bus too.
    const m0 = map.current;
    const onChoroplethClick = (e: maplibregl.MapMouseEvent): void => {
      const m = map.current;
      if (!m || !m.getLayer(REGION_CHOICE_FILL_LAYER_ID)) return;
      let hits: maplibregl.MapGeoJSONFeature[] = [];
      try {
        hits = m.queryRenderedFeatures(e.point, {
          layers: [REGION_CHOICE_FILL_LAYER_ID],
        });
      } catch {
        hits = [];
      }
      const id = hits[0]?.properties?.region_id;
      if (typeof id === "string") {
        // Stop the generic feature-inspect click handler from also firing a
        // popup for this tap (it's a pick, not an inspect).
        if (typeof (e as { preventDefault?: () => void }).preventDefault === "function") {
          (e as { preventDefault?: () => void }).preventDefault!();
        }
        regionChoiceBus.pickRegion(id);
      }
    };
    const onChoroplethMove = (e: maplibregl.MapMouseEvent): void => {
      const m = map.current;
      if (!m || !m.getLayer(REGION_CHOICE_FILL_LAYER_ID)) return;
      let hits: maplibregl.MapGeoJSONFeature[] = [];
      try {
        hits = m.queryRenderedFeatures(e.point, {
          layers: [REGION_CHOICE_FILL_LAYER_ID],
        });
      } catch {
        hits = [];
      }
      const id = hits[0]?.properties?.region_id;
      regionChoiceBus.setHovered(typeof id === "string" ? id : null);
      try {
        m.getCanvas().style.cursor = typeof id === "string" ? "pointer" : "";
      } catch {
        /* canvas gone */
      }
    };
    if (m0) {
      m0.on("click", onChoroplethClick);
      m0.on("mousemove", onChoroplethMove);
    }

    return () => {
      unsub();
      const m = map.current;
      if (m) {
        try {
          m.off("click", onChoroplethClick);
          m.off("mousemove", onChoroplethMove);
          clearRegionChoropleth(m);
        } catch {
          /* map torn down */
        }
      }
    };
  }, []);

  // job-0321 (F43) — resolve the legend placement.
  //   - aoiBbox + on-screen anchor → hang off the box's bottom edge, nudged
  //     down a small gap so it clears the dashed outline. On mobile the box can
  //     sit behind the collapsed bottom sheet, so we add the same ~116px sheet
  //     clearance App used for the old bottom-center mobile legend — but only as
  //     a floor: if the anchored position is already higher than that, keep it.
  //   - no anchor (AOI-less / off-screen / no map yet) → null, and LayerLegend
  //     falls back to its own bottom-center placement.
  const LEGEND_GAP_PX = 10; // small gap below the bbox bottom edge.
  const MOBILE_SHEET_CLEARANCE_PX = 116; // matches the prior App mobile offset.
  let resolvedAnchor: LegendAnchor | null = null;
  if (legendAnchor) {
    let top = legendAnchor.top + LEGEND_GAP_PX;
    if (isMobile) {
      // Keep the legend above the collapsed bottom sheet. We can only clamp in
      // screen space relative to the container; the container fills the map, so
      // its height is the canvas height. Use the projected-canvas height if we
      // can read it, else leave the anchored top as-is.
      const cur = map.current;
      let canvasH: number | null = null;
      try {
        const c = cur?.getCanvas();
        if (c) canvasH = c.clientHeight;
      } catch {
        canvasH = null;
      }
      if (canvasH != null) {
        const maxTop = canvasH - MOBILE_SHEET_CLEARANCE_PX;
        if (top > maxTop) top = Math.max(0, maxTop);
      }
    }
    resolvedAnchor = { left: legendAnchor.left, top };
  }

  return (
    <div
      ref={container}
      data-testid="grace2-map"
      style={{ position: "absolute", inset: 0 }}
    >
      {/* job-0321 (F43) — the legend now lives INSIDE the map container so it
          can anchor to the AOI box. `anchor` non-null = hang off the box's
          bottom edge; null = LayerLegend's own bottom-center fallback. */}
      {/* job-0321 (F43) anchor + FIX 4 (barWidth) — the legend hangs off the AOI
          box's bottom edge and is sized to the box's on-screen width. */}
      <LayerLegend
        layers={legendLayers}
        anchor={resolvedAnchor}
        barWidth={legendBarWidth}
      />

      {/* F74b / FIX 2 / FIX 3 — feature-click/tap-to-inspect popup. Shown when a
          click/tap hits a rendered vector feature; PINNED TO THE FEATURE'S MAP
          LOCATION (re-projected on pan/zoom so it pans with the map) and SCALED
          with the map zoom (shrinks zoomed out, grows zoomed in; clamped). It
          PERSISTS until the user taps elsewhere (a no-hit click dismisses it),
          taps another feature (it moves there), or hits the X / Esc — and the
          generic feature HIGHLIGHT (FIX 1) is cleared on any of those. */}
      {featurePopup ? (
        <FeaturePopup
          data={featurePopup}
          canvasSize={mapCanvasSize}
          isMobile={isMobile}
          currentZoom={currentZoom ?? undefined}
          onClose={() => {
            setFeaturePopup(null);
            // FIX 1 — clear the highlight when the popup is dismissed (X / Esc).
            const m = map.current;
            if (m) {
              try {
                clearFeatureHighlight(m);
              } catch {
                /* best effort */
              }
            }
          }}
        />
      ) : null}
    </div>
  );
}
