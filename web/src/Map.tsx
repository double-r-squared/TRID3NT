// GRACE-2 web — MapLibre GL JS CONUS basemap.
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
// The client renders, it never computes — every number on the map is a
// MapLibre-internal coordinate (Invariant 1 preserved trivially).

import { useEffect, useRef } from "react";
import maplibregl, { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

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

export function MapView(): JSX.Element {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);

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
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current = m;
    activeMap = m;

    return () => {
      m.remove();
      map.current = null;
      if (activeMap === m) activeMap = null;
    };
  }, []);

  return (
    <div
      ref={container}
      data-testid="grace2-map"
      style={{ position: "absolute", inset: 0 }}
    />
  );
}
