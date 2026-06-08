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

import { useEffect, useRef } from "react";
import maplibregl, { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { MapCommandPayload, SessionStatePayload } from "./contracts";

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

const WMS_BASE_URL: string =
  (import.meta.env.VITE_GRACE2_WMS_URL as string | undefined) ?? DEFAULT_WMS_URL;

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
    // Active: QGIS Server WMS proxying OSM raster tiles. Authoritative
    // Tier B rendering path (Invariant 4).
    "qgis-wms": {
      type: "raster",
      tiles: [WMS_TILE_TEMPLATE],
      tileSize: 256,
      attribution: QGIS_WMS_ATTRIBUTION,
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
}

// Extended map-command discriminator: contracts.ts only mirrors the 5 layer-CRUD
// verbs (zoom-to etc. are deferred to M4-M5 per job-0025 scope). Map.tsx
// handles zoom-to from the bus (dev-injection + future WS routing). We use
// a widened local type so the switch is type-safe without editing frozen contracts.ts.
interface ZoomToCommand {
  command: "zoom-to";
  args: { bbox: number[] };
}
type WireMapCommand = MapCommandPayload | ZoomToCommand;

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
 * Build the WMS tile URL for a given base WMS URL (which already includes
 * LAYERS=...). MapLibre substitutes {bbox-epsg-3857} per tile.
 * Invariant 4: QGIS Server renders; client just registers the URL.
 *
 * The base URL must already carry MAP= and LAYERS= (the agent emits the full
 * QGIS Server endpoint per `flood-emission-contract.md`). This helper appends
 * the per-tile WMS GetMap parameter set MapLibre's raster source needs.
 */
export function buildWmsTileUrl(wmsUrl: string): string {
  return `${wmsUrl}&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&CRS=EPSG:3857&FORMAT=image%2Fpng&TRANSPARENT=true&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256&STYLES=`;
}

// Layer + source IDs for the swappable basemap. The light basemap source is
// the QGIS Server WMS proxy (already in the seed style); the dark basemap
// source is added/removed at runtime when the theme changes.
const BASEMAP_LAYER_ID = "qgis-basemap";
const BASEMAP_SOURCE_ID = "qgis-wms";
const DARK_BASEMAP_LAYER_ID = "carto-dark-basemap";
const DARK_BASEMAP_SOURCE_ID = "carto-dark";

export function MapView({ subscribeSessionState, subscribeMapCommand, theme = "light" }: MapViewProps = {}): JSX.Element {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);
  // useRef so this survives effect re-runs without triggering re-render (A.7).
  const addedSourceIds = useRef<Set<string>>(new Set());
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
      attributionControl: { compact: false },
    });
    // Decision I: 2D-only navigation. Belt + suspenders — explicitly disable
    // rotation in addition to constructor options so a future MapLibre default
    // change can't silently re-enable it.
    m.touchZoomRotate.disableRotation();
    m.keyboard.disableRotation();
    // Navigation control at bottom-right so it doesn't overlap the Chat panel (top-right).
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.current = m;
    activeMap = m;

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
      // If style isn't loaded yet, the deferred idle handler will retry.
      if (!m.isStyleLoaded()) return;

      const currentLayers = payload.loaded_layers ?? [];
      const currentIds = new Set(currentLayers.map((l) => l.layer_id));

      // Remove layers that are gone (replace-not-reconcile).
      for (const id of addedSourceIds.current) {
        if (!currentIds.has(id)) {
          if (m.getLayer(id)) m.removeLayer(id);
          if (m.getSource(id)) m.removeSource(id);
          addedSourceIds.current.delete(id);
        }
      }

      // Add new layers; update opacity/visibility on existing.
      // Cast to WireLayerSummary: the agent emits `uri` on the wire even though
      // contracts.ts uses `source_url` (schema mismatch; tracked as OQ-0068-URI).
      for (const _layer of currentLayers) {
        const layer = _layer as unknown as WireLayerSummary;
        const opacity = layer.opacity ?? 1;
        const visible = layer.visible !== false;
        if (addedSourceIds.current.has(layer.layer_id)) {
          // Update paint/layout on existing layer.
          if (m.getLayer(layer.layer_id)) {
            m.setPaintProperty(layer.layer_id, "raster-opacity", opacity);
            m.setLayoutProperty(layer.layer_id, "visibility", visible ? "visible" : "none");
          }
        } else {
          // New layer — add source + layer. MapLibre paints layers in
          // insertion order; we don't pass an explicit beforeId here because
          // the basemap was added first via the seed style spec, so any
          // flood layer added now will paint ABOVE it (correct stacking).
          // The dark-theme swap path (`applyTheme` below) preserves this
          // invariant by re-adding the basemap with `beforeId =` first flood
          // layer, so flood overlays always stay on top of whichever
          // basemap is active.
          const tileUrl = buildWmsTileUrl(layer.uri);
          m.addSource(layer.layer_id, {
            type: "raster",
            tiles: [tileUrl],
            tileSize: 256,
          });
          m.addLayer({
            id: layer.layer_id,
            type: "raster",
            source: layer.layer_id,
            paint: { "raster-opacity": opacity },
            layout: { visibility: visible ? "visible" : "none" },
          });
          addedSourceIds.current.add(layer.layer_id);
        }
      }
    };

    const unsub = subscribeSessionState((payload) => {
      latestSessionState.current = payload;
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
    };

    applyTheme();
    // No cleanup — basemap state lives in the map's style; the next theme
    // change will reconcile it.
  }, [theme]);

  // Subscribe to map-command for zoom-to and transient camera/animation verbs
  // (job-0068, change 5 client side). Layer-CRUD verbs are DEFERRED (handled
  // via session-state per layer-emission-contract.md).
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
          const [minLon, minLat, maxLon, maxLat] = bbox as [number, number, number, number];
          m.fitBounds(
            [[minLon, minLat], [maxLon, maxLat]],
            { padding: 40, duration: 1200 },
          );
        }
      } else {
        // eslint-disable-next-line no-console
        console.warn("[MapView] MapCommand not yet implemented:", payload.command);
      }
    });
  }, [subscribeMapCommand]);

  return (
    <div
      ref={container}
      data-testid="grace2-map"
      style={{ position: "absolute", inset: 0 }}
    />
  );
}
