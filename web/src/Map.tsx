// GRACE-2 web — MapLibre GL JS CONUS basemap.
//
// FR-WC-1, FR-DT-1, Decision I:
//   - OSM raster tile basemap loaded directly from the public CDN with the
//     required "© OpenStreetMap contributors" attribution.
//   - Initial view fits CONUS (lng -95.5, lat 37, zoom 4).
//   - Camera locked 2D: maxPitch:0, dragRotate disabled, no touch rotate.
//   - Pan + zoom enabled. No layer panel, no scrubber, no pick-modes —
//     those are M3/M9 territory.
//
// The client renders, it never computes — every number on the map is a
// MapLibre-internal coordinate (Invariant 1 preserved trivially).

import { useEffect, useRef } from "react";
import maplibregl, { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

const OSM_TILE_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors';

const CONUS_VIEW = {
  center: [-95.5, 37.0] as [number, number],
  zoom: 4,
};

const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: [OSM_TILE_TEMPLATE],
      tileSize: 256,
      attribution: OSM_ATTRIBUTION,
      maxzoom: 19,
    },
  },
  layers: [
    {
      id: "osm-basemap",
      type: "raster",
      source: "osm",
      minzoom: 0,
      maxzoom: 22,
    },
  ],
};

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

    return () => {
      m.remove();
      map.current = null;
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
