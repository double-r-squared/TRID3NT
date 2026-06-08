// GRACE-2 web — vector layer rendering helpers (job-0139 + job-0146).
//
// Resolves OQ-PAY-MAP-VECTOR-UNSUPPORTED surfaced by the Playwright UI capture
// agent (2026-06-08): Map.tsx prior to this job only handled raster WMS layers.
// Vector layers added to `loaded_layers` (GBIF points, WDPA polygons, NWS alerts,
// OSM roads, MTBS burn perimeters, FIRMS active fire, eBird, IUCN ranges,
// Movebank tracks — 12+ Wave 1/1.5/2 fetchers all return `layer_type='vector'`)
// showed in `LayerPanel` but never rendered on the map.
//
// job-0146 adds:
//   - 12-colour curated palette replacing the FNV-1a generic colours (Part 1)
//   - Expanded style_preset registry with curated per-preset colours (Part 1)
//   - ds_mean choropleth expression builder for Pelicun damage layers (Part 2)
//   - Polygon fill opacity constant (0.4) for basemap-label readability (Part 3)
//
// This module is responsible for the data-fetch + style-derivation seam ONLY.
// MapLibre source/layer registration stays inside Map.tsx (per file-ownership
// boundary in the job-0139 kickoff §scope).
//
// Invariants preserved:
//   - 1. Determinism boundary: every coordinate/value rendered comes from the
//     fetched GeoJSON; we never compute new geographic numbers.
//   - 4. Rendering through QGIS Server: vector layers are agent-served
//     FlatGeobuf / GeoJSON URIs (NOT GCS direct reads); the web client
//     consumes pre-served bytes only.
//   - 5. Tier separation: the client never reaches `gs://` URLs directly —
//     vector URIs MUST be https://-style URLs handed off by the agent.
//
// Architecture note: per the job-0139 kickoff §2, we keep v0.1 simple by
// fetching FlatGeobuf via the npm `flatgeobuf` package + converting to GeoJSON
// in-browser. A future enhancement would stream-deserialize via the package's
// AsyncGenerator, but for v0.1 (Case 1 demo headline = single panther / spoonbill
// / alligator collection sized in the low-thousands of features), in-memory
// collection + a single addSource is the right altitude.

import type { FeatureCollection, Feature, Geometry } from "geojson";
import { deserialize } from "flatgeobuf/lib/mjs/geojson.js";

/** Geometry families MapLibre paints distinctly. */
export type VectorGeomKind = "point" | "line" | "polygon" | "unknown";

/** Result of `fetchVectorAsGeoJson`. */
export interface VectorFetchResult {
  featureCollection: FeatureCollection;
  geomKind: VectorGeomKind;
}

/**
 * Classify the geometry of the first non-null feature in a FeatureCollection.
 * MapLibre needs the geometry family up-front to pick the layer type
 * (circle / line / fill). Multi* variants collapse to their base kind.
 */
export function detectGeomKind(fc: FeatureCollection): VectorGeomKind {
  for (const f of fc.features) {
    const g: Geometry | null = f.geometry as Geometry | null;
    if (!g) continue;
    switch (g.type) {
      case "Point":
      case "MultiPoint":
        return "point";
      case "LineString":
      case "MultiLineString":
        return "line";
      case "Polygon":
      case "MultiPolygon":
        return "polygon";
      // GeometryCollection: inspect first sub-geometry.
      case "GeometryCollection": {
        const sub = g.geometries[0];
        if (sub) {
          switch (sub.type) {
            case "Point":
            case "MultiPoint":
              return "point";
            case "LineString":
            case "MultiLineString":
              return "line";
            case "Polygon":
            case "MultiPolygon":
              return "polygon";
          }
        }
        return "unknown";
      }
      default:
        return "unknown";
    }
  }
  return "unknown";
}

/**
 * Fetch a vector layer URI and return it as a GeoJSON FeatureCollection plus
 * its geometry kind. Supports:
 *   - .fgb (FlatGeobuf): parsed via the `flatgeobuf` npm package.
 *   - .geojson / .json: fetched + JSON-parsed directly.
 *
 * `uri` MUST be an https://-style URL — the client never fetches `gs://`
 * directly (Invariant 5; the agent rewrites GCS pointers to served URLs).
 *
 * Throws on:
 *   - non-2xx HTTP status
 *   - malformed FlatGeobuf bytes (the underlying parser raises)
 *   - GeoJSON that does not parse to a FeatureCollection
 *
 * The caller is expected to catch + log per-layer failures so one bad layer
 * does not break the entire map.
 */
export async function fetchVectorAsGeoJson(
  uri: string,
  fetchImpl: typeof fetch = fetch,
): Promise<VectorFetchResult> {
  if (uri.startsWith("gs://")) {
    // Invariant 5 guardrail. The agent should never hand us a `gs://` URL;
    // surface loudly rather than silently fail.
    throw new Error(
      `[vector_rendering] refusing to fetch gs:// URL from client (invariant 5): ${uri}`,
    );
  }

  const isFgb = /\.fgb(\?|$)/i.test(uri);

  if (isFgb) {
    // FlatGeobuf path: fetch bytes, parse via the flatgeobuf package, collect
    // features into a FeatureCollection. For v0.1 we materialise the full
    // collection (kickoff §scope: "convert to GeoJSON using the flatgeobuf
    // npm package"); streaming render is a future enhancement.
    const resp = await fetchImpl(uri);
    if (!resp.ok) {
      throw new Error(
        `[vector_rendering] fetch FlatGeobuf failed: ${resp.status} ${resp.statusText} (${uri})`,
      );
    }
    const buf = await resp.arrayBuffer();
    const typedArray = new Uint8Array(buf);
    const features: Feature[] = [];
    // deserialize returns an AsyncGenerator<IGeoJsonFeature> — collect.
    for await (const feat of deserialize(typedArray)) {
      features.push(feat as Feature);
    }
    const fc: FeatureCollection = { type: "FeatureCollection", features };
    return { featureCollection: fc, geomKind: detectGeomKind(fc) };
  }

  // GeoJSON path.
  const resp = await fetchImpl(uri);
  if (!resp.ok) {
    throw new Error(
      `[vector_rendering] fetch GeoJSON failed: ${resp.status} ${resp.statusText} (${uri})`,
    );
  }
  const data = (await resp.json()) as unknown;
  if (
    !data ||
    typeof data !== "object" ||
    (data as { type?: string }).type !== "FeatureCollection" ||
    !Array.isArray((data as { features?: unknown }).features)
  ) {
    throw new Error(
      `[vector_rendering] not a FeatureCollection: ${uri}`,
    );
  }
  const fc = data as FeatureCollection;
  return { featureCollection: fc, geomKind: detectGeomKind(fc) };
}

// ---------------------------------------------------------------------------
// Style derivation (job-0146 — curated palette + preset registry)
// ---------------------------------------------------------------------------

/**
 * Curated 12-colour categorical palette for vector layers without a style_preset.
 * Designed for (job-0146 Part 1):
 *   - High contrast against CartoDB DarkMatter dark basemap
 *   - Color-blind friendliness (avoids problematic red/green pairs as sole
 *     distinguishers; uses hue + lightness variance)
 *   - Distinctiveness when 6+ species layers are stacked simultaneously
 *
 * Palette rationale (by slot):
 *   0  #FF7F0E  orange         — large mammals (panther, bear)
 *   1  #00BFFF  bright cyan    — birds (spoonbill, roseate, wading)
 *   2  #ADFF2F  lime green     — reptiles (alligator, sea turtle)
 *   3  #40E0D0  aqua/turquoise — marine species
 *   4  #FF1493  deep pink      — plants / flora
 *   5  #708090  slate grey     — admin boundaries (WDPA, census)
 *   6  #FF4444  fire red       — fire data (MTBS, FIRMS fallback)
 *   7  #4477FF  sky blue       — flood / hydrological data
 *   8  #FFD700  gold           — roads / infrastructure
 *   9  #DA70D6  orchid         — generic fallback 1
 *  10  #98FF98  pale green     — generic fallback 2
 *  11  #FFA07A  light salmon   — generic fallback 3
 *
 * The palette is exported for tests; callers use `paletteColorFor(layerId)`.
 */
export const VECTOR_PALETTE: readonly string[] = [
  "#FF7F0E", // orange        — large mammals
  "#00BFFF", // bright cyan   — birds
  "#ADFF2F", // lime green    — reptiles
  "#40E0D0", // aqua          — marine
  "#FF1493", // deep pink     — plants
  "#708090", // slate grey    — admin/boundaries
  "#FF4444", // fire red      — fire data
  "#4477FF", // sky blue      — flood/hydro
  "#FFD700", // gold          — roads/infra
  "#DA70D6", // orchid        — generic 1
  "#98FF98", // pale green    — generic 2
  "#FFA07A", // light salmon  — generic 3
];

/**
 * Deterministic palette colour for a given layer_id. Uses a simple
 * 32-bit FNV-1a hash so the same layer_id always gets the same colour
 * across reloads (cheap-and-stable: cryptographic hashing is overkill,
 * and we want determinism more than collision-resistance).
 */
export function paletteColorFor(layerId: string): string {
  // FNV-1a 32-bit
  let h = 0x811c9dc5;
  for (let i = 0; i < layerId.length; i++) {
    h ^= layerId.charCodeAt(i);
    // multiply by FNV prime mod 2^32
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return VECTOR_PALETTE[h % VECTOR_PALETTE.length] ?? VECTOR_PALETTE[0]!;
}

/**
 * Map a vector layer's `style_preset` (if any) to a primary colour. Returns
 * `undefined` when no preset is known, signalling the caller to fall back to
 * `paletteColorFor`. Mirrors `style-presets.ts` but operates on vector layer
 * colours (the raster preset uses gradient stops; vectors only need a single
 * primary colour for circle/line/fill paint).
 *
 * Curated preset registry (job-0146 Part 1):
 *   - 'gbif_occurrences'        → orange  #FF7F0E
 *   - 'inaturalist_observations' → bright cyan #00BFFF
 *   - 'wdpa_protected_areas', 'wdpa_polygon', 'wdpa', 'protected_area'
 *                               → slate grey #708090
 *   - 'nws_alerts', 'nws_alert', 'nws_warning', 'alert'
 *                               → fire red #FF4444
 *   - 'mtbs_burn_severity', 'burn_perimeter', 'mtbs'
 *                               → fire red #FF4444
 *   - 'firms_active_fire', 'firms', 'active_fire'
 *                               → bright red #FF4444
 *   - 'osm_roads', 'osm_road', 'roads'
 *                               → gold/muted yellow #FFD700
 *   - 'pelicun_damage'          → special: uses ds_mean choropleth expression
 *                                 (caller must invoke buildDsMeanExpression)
 *
 * Note: 'pelicun_damage' returns a sentinel string so the caller knows to
 * use the choropleth expression path (Part 2) rather than a flat colour.
 *
 * Extend as engine specialists land vector style presets.
 */
export const PELICUN_DAMAGE_PRESET = "pelicun_damage" as const;

export function presetColorFor(stylePreset: string | null | undefined): string | undefined {
  if (!stylePreset) return undefined;
  const key = stylePreset.toLowerCase();
  // Species / biodiversity fetchers
  if (key === "gbif_occurrences" || key.startsWith("gbif")) return "#FF7F0E";
  if (key === "inaturalist_observations" || key.startsWith("inat")) return "#00BFFF";
  // Protected areas / boundaries
  if (key.includes("wdpa") || key.includes("protected_area")) return "#708090";
  // Alerts
  if (key.includes("nws_alert") || key.includes("nws_warning") || key.includes("alert")) return "#FF4444";
  // Fire data
  if (key.includes("burn_perimeter") || key.includes("mtbs")) return "#FF4444";
  if (key.includes("firms") || key.includes("active_fire")) return "#FF4444";
  // Roads / infrastructure
  if (key.includes("osm_road") || key === "roads" || key === "osm_roads") return "#FFD700";
  // Pelicun damage: sentinel — caller must use choropleth expression
  if (key === PELICUN_DAMAGE_PRESET) return PELICUN_DAMAGE_PRESET;
  return undefined;
}

/**
 * Fill opacity for polygon layers (Part 3). Reduced to 0.4 (from 0.5) so
 * basemap labels remain readable underneath polygon fill. The caller should
 * multiply this by the layer opacity setting.
 */
export const POLYGON_FILL_OPACITY = 0.4;

/**
 * Stroke width for polygon outline layers (Part 3). 1.5px so polygon edges
 * remain visible against the lower fill opacity.
 */
export const POLYGON_STROKE_WIDTH = 1.5;

/**
 * Build a MapLibre `fill-color` expression mapping a `ds_mean` property
 * (0–1 damage state mean) through a green → yellow → red gradient (Part 2).
 *
 * The interpolation uses three stops:
 *   0.0 → green  #2DC937 (no damage)
 *   0.5 → yellow #E7B416 (moderate damage)
 *   1.0 → red    #CC3232 (heavy damage)
 *
 * When `ds_mean` is absent the fallback color is slate (#708090) to visually
 * distinguish "damage data missing" from any point in the gradient.
 *
 * The expression is a MapLibre-native expression array so it can be assigned
 * directly to `paint["fill-color"]` without any runtime JS interpolation on
 * the client (invariant 1: we emit received values, not computed numbers).
 */
export function buildDsMeanExpression(): unknown[] {
  return [
    "case",
    ["has", "ds_mean"],
    [
      "interpolate",
      ["linear"],
      ["get", "ds_mean"],
      0.0, "#2DC937",  // green  — no damage
      0.5, "#E7B416",  // yellow — moderate damage
      1.0, "#CC3232",  // red    — heavy damage
    ],
    "#708090",  // fallback: slate — ds_mean absent
  ];
}

/**
 * Cluster source configuration parameters for dense point layers (Part 4).
 * When a point FeatureCollection has > CLUSTER_THRESHOLD features, Map.tsx
 * should create the GeoJSON source with clustering enabled using these params.
 */
export const CLUSTER_THRESHOLD = 500;
export const CLUSTER_RADIUS = 50;

/** Final colour to use for a vector layer (preset > palette). */
export function resolveVectorColor(
  layerId: string,
  stylePreset: string | null | undefined,
): string {
  const preset = presetColorFor(stylePreset);
  // If the preset is the pelicun sentinel, fall back to a neutral grey
  // since the real color comes from buildDsMeanExpression()
  if (preset === PELICUN_DAMAGE_PRESET) return "#708090";
  return preset ?? paletteColorFor(layerId);
}

/**
 * Returns true when the layer should use the Pelicun ds_mean choropleth
 * expression instead of a flat fill-color.
 */
export function isPelicunDamageLayer(stylePreset: string | null | undefined): boolean {
  if (!stylePreset) return false;
  return stylePreset.toLowerCase() === PELICUN_DAMAGE_PRESET;
}
