// GRACE-2 web — vector layer rendering helpers (job-0139).
//
// Resolves OQ-PAY-MAP-VECTOR-UNSUPPORTED surfaced by the Playwright UI capture
// agent (2026-06-08): Map.tsx prior to this job only handled raster WMS layers.
// Vector layers added to `loaded_layers` (GBIF points, WDPA polygons, NWS alerts,
// OSM roads, MTBS burn perimeters, FIRMS active fire, eBird, IUCN ranges,
// Movebank tracks — 12+ Wave 1/1.5/2 fetchers all return `layer_type='vector'`)
// showed in `LayerPanel` but never rendered on the map.
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
// Style derivation
// ---------------------------------------------------------------------------

/**
 * A 12-colour categorical palette for vector layers without a style_preset.
 * Colours chosen for high contrast on both light and dark basemaps. Hex
 * (rgb) values pulled from the qualitative "Set3" + "Dark2" matplotlib
 * palettes, hand-selected for legibility against a CartoDB DarkMatter +
 * OSM basemap.
 *
 * The palette is exported for tests; callers use `paletteColorFor(layerId)`.
 */
export const VECTOR_PALETTE: readonly string[] = [
  "#e41a1c", // red
  "#377eb8", // blue
  "#4daf4a", // green
  "#984ea3", // purple
  "#ff7f00", // orange
  "#ffff33", // yellow
  "#a65628", // brown
  "#f781bf", // pink
  "#1b9e77", // teal
  "#d95f02", // dark orange
  "#7570b3", // slate
  "#66a61e", // olive
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
 * Known presets currently:
 *   - any preset whose name begins with `species_*` → the engine encodes the
 *     species colour into the preset name; until the engine surfaces a
 *     formal vector-preset registry, those are routed through the palette
 *     hash for determinism (already gives per-species stable colours).
 *   - `wdpa_polygon`, `wdpa`, `protected_area` → semi-transparent green
 *     (common WDPA cartography convention).
 *   - `nws_alert`, `nws_warning` → red-orange.
 *   - `osm_road`, `roads` → dark grey.
 *
 * Extend as engine specialists land vector style presets.
 */
export function presetColorFor(stylePreset: string | null | undefined): string | undefined {
  if (!stylePreset) return undefined;
  const key = stylePreset.toLowerCase();
  if (key.includes("wdpa") || key.includes("protected_area")) return "#2ca25f";
  if (key.includes("nws_alert") || key.includes("nws_warning") || key.includes("alert")) return "#e6550d";
  if (key.includes("osm_road") || key === "roads") return "#525252";
  if (key.includes("burn_perimeter") || key.includes("mtbs")) return "#bd0026";
  if (key.includes("firms") || key.includes("active_fire")) return "#fd8d3c";
  return undefined;
}

/** Final colour to use for a vector layer (preset > palette). */
export function resolveVectorColor(
  layerId: string,
  stylePreset: string | null | undefined,
): string {
  return presetColorFor(stylePreset) ?? paletteColorFor(layerId);
}
