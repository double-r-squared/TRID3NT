// GRACE-2 web - 3D terrain mode (FIRST CUT, "3D terrain viz" project track).
//
// A pure, MapLibre-free, DOM-light module that owns:
//   1. The user's persisted 3D-terrain enable flag (localStorage, default OFF,
//      mirroring the bbox_progress / theme persistence pattern).
//   2. The persisted contour-overlay enable flag (also default OFF). The contour
//      LINE rendering is intentionally STUBBED for the first cut - see the
//      CONTOUR DECISION note below.
//   3. The terrain-RGB DEM source descriptor builder (`buildTerrainDemSource`) -
//      pure, so it is trivially unit-testable without a WebGL canvas.
//
// Map.tsx imports the source builder + the apply/teardown helpers and wires them
// to the live MapLibre instance behind a `terrain3dEnabled` prop. Everything that
// touches the map lives in the thin `applyTerrain3d` / `removeTerrain3d` helpers
// at the bottom; the rest is pure and unit-tested.
//
// ---------------------------------------------------------------------------
// DEM SOURCE (terrain-RGB) - where the elevation comes from
// ---------------------------------------------------------------------------
// MapLibre's hillshade + setTerrain need a `raster-dem` source whose PNG tiles
// encode elevation. Two encodings exist: "mapbox" (Mapbox Terrain-RGB) and
// "terrarium" (Mapzen/Tilezen Terrarium). We support both.
//
// Per the kickoff the PREFERRED origin is TiTiler's `/cog/tiles` rendering a DEM
// COG as terrain-RGB, served off `publicTileBase()` (the single CloudFront edge,
// VITE_GRACE2_PUBLIC_BASE). TiTiler's COG endpoint exposes a `?return_mask`-free
// terrain-RGB via the `terrainrgb` colormap / algorithm, BUT it needs a concrete
// DEM COG `url` to render - which is a PER-CASE artifact the agent does not yet
// publish (see FOLLOW-UPS below). So the first cut degrades cleanly:
//
//   primary  : a TiTiler /cog/tiles terrain-RGB template, built ONLY when both
//              the public edge base AND a DEM COG url are supplied. This is the
//              forward path once the agent emits a per-case DEM COG.
//   fallback : the public AWS Terrain Tiles (Terrarium) open dataset on S3 -
//              global 1-arc-second-ish coverage, no key, CC0/ODbL. This makes
//              3D mode WORK TODAY for any AOI with zero backend changes, which is
//              the whole point of a first cut.
//
// The encoding follows the source: TiTiler terrain-RGB -> "mapbox"; AWS Terrain
// Tiles -> "terrarium".
//
// ---------------------------------------------------------------------------
// CONTOUR DECISION (the quick win)
// ---------------------------------------------------------------------------
// MapLibre GL JS has NO native contour-from-DEM source. Drawing iso-contour
// LINES from a terrain-RGB DEM requires the `maplibre-contour` plugin (it builds
// a DEM-backed vector source the style can stroke). `maplibre-contour` is NOT a
// dependency of this app (see web/package.json - only `maplibre-gl` +
// `terra-draw-maplibre-gl-adapter`), and the kickoff says: do NOT add a heavy
// dep for the first cut - STUB the contour toggle with a TODO instead.
//
// So: the contour toggle is persisted + surfaced in Settings (so the UX seam is
// real), but `applyTerrain3d` only logs a one-time TODO when contours are
// requested. Wiring it for real = add `maplibre-contour`, register a
// `mlcontour.DemSource`, and add a `line` layer over its contour vector source.
// Tracked as a follow-up.

import { publicTileBase } from "./public_base";

// --- persistence: 3D terrain enable flag --------------------------------- //

/**
 * localStorage key for the 3D-terrain enable flag. DEFAULT OFF (3D is opt-in):
 * an absent / unparseable value reads as disabled, so a fresh user keeps the
 * flat 2D map. Mirrors LS_BBOX_ANIM (a single per-user key, read-with-default,
 * write-through).
 */
export const LS_TERRAIN_3D = "grace2.terrain3d";

/** Read the persisted 3D-terrain enable flag. Default OFF (only "true" enables). */
export function readTerrain3dEnabled(): boolean {
  try {
    return localStorage.getItem(LS_TERRAIN_3D) === "true";
  } catch {
    return false;
  }
}

/** Persist the 3D-terrain enable flag. */
export function writeTerrain3dEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(LS_TERRAIN_3D, enabled ? "true" : "false");
  } catch {
    /* storage unavailable (private mode / SSR) - non-fatal */
  }
}

// --- persistence: contour overlay enable flag (STUB) --------------------- //

/**
 * localStorage key for the contour-overlay enable flag. DEFAULT OFF. The
 * rendering is stubbed (see CONTOUR DECISION above); the flag still persists so
 * the toggle is a real UX seam and a future maplibre-contour wire-up reads it.
 */
export const LS_CONTOURS = "grace2.terrainContours";

/** Read the persisted contour-overlay enable flag. Default OFF. */
export function readContoursEnabled(): boolean {
  try {
    return localStorage.getItem(LS_CONTOURS) === "true";
  } catch {
    return false;
  }
}

/** Persist the contour-overlay enable flag. */
export function writeContoursEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(LS_CONTOURS, enabled ? "true" : "false");
  } catch {
    /* storage unavailable - non-fatal */
  }
}

// --- terrain-RGB DEM source descriptor builder --------------------------- //

/** Stable MapLibre source / layer ids for the 3D terrain stack. */
export const TERRAIN_DEM_SOURCE_ID = "grace2-terrain-dem";
export const TERRAIN_HILLSHADE_LAYER_ID = "grace2-terrain-hillshade";
export const TERRAIN_SKY_LAYER_ID = "grace2-terrain-sky";

/** Default vertical exaggeration for setTerrain. 2.0 makes relief read STRONGLY
 *  at AOI / city scale (the zoom NATE works at) once the camera is pitched - the
 *  earlier 1.4 was too subtle and, with a flat top-down camera, made "3D mode"
 *  look like a mere hillshade filter. 2.0 sits below the spiky-artifact regime of
 *  2.5+ while reading as dramatic, navigable relief. Named export so it stays
 *  the single source of truth (unit-tested + asserted by Map.tsx wiring). */
export const TERRAIN_EXAGGERATION = 2.0;

// --- pure camera-pose builders (Priority 1: make 3D actually LOOK 3D) ------ //
//
// The toggle used to add a DEM + hillshade + setTerrain and UNLOCK pitch/rotate
// but never PITCHED the camera, so enabling 3D left a flat top-down view and the
// user only ever saw hillshade shading - never the relief. These builders are
// pure (no MapLibre) so Map.tsx can easeTo() the live camera into / out of a 3D
// pose, and the exact poses are unit-tested.

/** A camera pose easeTo() accepts (the subset Map.tsx hands to easeTo). */
export interface CameraPose {
  pitch: number;
  bearing: number;
}

/** The pitched 3D pose enabling 3D eases the camera INTO. ~67deg pitch reads as
 *  strong relief at AOI scale while staying under setMaxPitch(75); a gentle 25deg
 *  bearing turns the terrain off-axis so ridges / valleys read as depth rather
 *  than a head-on wall. Center + zoom are preserved by easeTo (not set here). */
export const TERRAIN_3D_PITCH = 67;
export const TERRAIN_3D_BEARING = 25;

/** The flat 2D pose disabling 3D eases the camera back to (top-down, north-up)
 *  BEFORE the terrain stack is torn down and the camera re-locked to 2D. */
export const FLAT_2D_PITCH = 0;
export const FLAT_2D_BEARING = 0;

/** easeTo duration (ms) for the 3D enter / exit camera move. ~1.2s reads as a
 *  smooth, deliberate "lift into 3D" without feeling sluggish. */
export const TERRAIN_3D_EASE_MS = 1200;

/** The pitched 3D camera pose (pure). easeTo merges this over the live center /
 *  zoom, so enabling 3D tilts in place. */
export function buildTerrain3dCameraPose(): CameraPose {
  return { pitch: TERRAIN_3D_PITCH, bearing: TERRAIN_3D_BEARING };
}

/** The flat top-down 2D camera pose (pure) the disable path eases back to. */
export function buildFlat2dCameraPose(): CameraPose {
  return { pitch: FLAT_2D_PITCH, bearing: FLAT_2D_BEARING };
}

/**
 * Public AWS Terrain Tiles (Terrarium encoding) open dataset. No API key,
 * global coverage, served from S3 over https. This is the zero-backend fallback
 * that makes 3D mode work TODAY. `tileSize` 256, encoding "terrarium".
 * Ref: https://registry.opendata.aws/terrain-tiles/
 */
export const AWS_TERRAIN_TERRARIUM_TEMPLATE =
  "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

/** A MapLibre `raster-dem` source spec (the subset Map.tsx hands to addSource). */
export interface TerrainDemSourceSpec {
  type: "raster-dem";
  tiles: [string];
  tileSize: number;
  encoding: "mapbox" | "terrarium";
  maxzoom: number;
  attribution: string;
  /** Where the source came from - surfaced for diagnostics / tests. */
  origin: "titiler" | "aws-terrarium";
}

/** Options for `buildTerrainDemSource`. */
export interface BuildTerrainDemOptions {
  /**
   * A DEM COG url (e.g. an s3:// or https:// COG the agent published for the
   * active case). When supplied AND a public edge base exists, the primary
   * TiTiler terrain-RGB template is built against it. Absent => fallback.
   */
  demCogUrl?: string | null;
  /**
   * The public edge base. Defaults to `publicTileBase()`. Threaded for tests.
   * When null/absent the TiTiler path is unavailable -> fallback.
   */
  publicBase?: string | null;
}

/**
 * Build the terrain-RGB DEM source descriptor.
 *
 * Primary (forward path): TiTiler `/cog/tiles/{z}/{x}/{y}.png` rendering the
 * supplied DEM COG as Mapbox terrain-RGB, off the public CloudFront edge. Used
 * only when BOTH `demCogUrl` and a public base are present.
 *
 * Fallback (works today, no backend): AWS Terrain Tiles (Terrarium).
 *
 * Pure: no MapLibre, no network. Returns the source spec for addSource().
 */
export function buildTerrainDemSource(
  opts: BuildTerrainDemOptions = {},
): TerrainDemSourceSpec {
  const base = opts.publicBase !== undefined ? opts.publicBase : publicTileBase();
  const demCogUrl = opts.demCogUrl ?? null;

  if (base && demCogUrl) {
    // TiTiler terrain-RGB: the /cog/tiles endpoint renders a DEM COG into
    // Mapbox Terrain-RGB PNGs. `?url=<cog>` selects the COG; the `terrainrgb`
    // colormap is the rgb-encoded-elevation algorithm TiTiler ships for exactly
    // this MapLibre raster-dem use case. The base has no trailing slash
    // (normalizePublicBase guarantees it), and /cog/* is already a CloudFront
    // behavior routed to TiTiler (see public_base.ts).
    const tpl =
      `${base}/cog/tiles/{z}/{x}/{y}.png` +
      `?url=${encodeURIComponent(demCogUrl)}&colormap_name=terrainrgb`;
    return {
      type: "raster-dem",
      tiles: [tpl],
      tileSize: 256,
      encoding: "mapbox",
      maxzoom: 18,
      attribution: "Elevation via GRACE-2 TiTiler (terrain-RGB)",
      origin: "titiler",
    };
  }

  // Fallback: the public AWS Terrain Tiles Terrarium dataset.
  return {
    type: "raster-dem",
    tiles: [AWS_TERRAIN_TERRARIUM_TEMPLATE],
    tileSize: 256,
    encoding: "terrarium",
    maxzoom: 15,
    attribution:
      'Elevation - <a href="https://registry.opendata.aws/terrain-tiles/" target="_blank" rel="noopener noreferrer">AWS Terrain Tiles</a>',
    origin: "aws-terrarium",
  };
}

// --- LANE E: 3D AOI line-layer pulse-glow -------------------------------- //
//
// In 3D the camera is pitched/rotated, so the 2D axis-aligned scan overlay
// (BboxProgressOverlay) no longer traces the tilted AOI box and "looks weird"
// (NATE). resolveBboxProgress returns mode "none" in 3D; this helper carries the
// "working" cue INSTEAD by pulsing the REAL on-map AOI line layer
// (ANALYSIS_EXTENT_LINE_LAYER_ID) bright -> normal -> bright. Because the line
// layer is geographic geometry it drapes over terrain and follows the camera
// automatically, so the glow always hugs the box.
//
// A single requestAnimationFrame loop drives a sine wave over line-width,
// line-opacity, and line-blur (the blur ramps up at the bright peak to read as a
// true glow halo). The caller owns start/stop; stop() restores the static paint.

/** Structural subset of maplibre-gl Map the pulse-glow needs (paint mutation). */
export interface PulseGlowMapLike {
  getLayer(id: string): unknown;
  setPaintProperty(layerId: string, name: string, value: unknown): void;
}

/** A handle returned by `startAoiPulseGlow`; call `stop()` to end the loop and
 *  restore the static AOI line paint. Idempotent. */
export interface AoiPulseGlowHandle {
  stop(): void;
}

/** The static (non-glowing) AOI line paint, restored on stop. Matches the
 *  drawAnalysisExtent defaults (line-width 1.5, line-opacity 0.9, no blur). */
const AOI_STATIC_WIDTH = 1.5;
const AOI_STATIC_OPACITY = 0.9;
const AOI_STATIC_BLUR = 0;

/** Glow peak/trough metrics. ~1.6s period matches the existing
 *  grace2-bbox-border-pulse cadence; width 1.5<->3.5, opacity 0.55<->1.0,
 *  blur 0<->2 reads as a bright<->normal pulse with a halo at the peak. */
const GLOW_PERIOD_MS = 1600;
const GLOW_WIDTH_MIN = 1.5;
const GLOW_WIDTH_MAX = 3.5;
const GLOW_OPACITY_MIN = 0.55;
const GLOW_OPACITY_MAX = 1.0;
const GLOW_BLUR_MAX = 2;

/**
 * Start a pulse-glow rAF loop on the AOI line layer. Returns a handle whose
 * `stop()` cancels the loop and restores the static paint. Defensive: a missing
 * layer / torn-down map / absent rAF (SSR / test env) makes start a safe no-op
 * (stop is still callable). The loop self-cancels if the layer disappears.
 *
 * @param m       the live map (needs getLayer + setPaintProperty)
 * @param layerId the AOI line layer id (ANALYSIS_EXTENT_LINE_LAYER_ID)
 */
export function startAoiPulseGlow(
  m: PulseGlowMapLike,
  layerId: string,
): AoiPulseGlowHandle {
  let rafId: number | null = null;
  let stopped = false;
  const hasRaf =
    typeof requestAnimationFrame === "function" &&
    typeof cancelAnimationFrame === "function";

  const setStatic = (): void => {
    try {
      if (!m.getLayer(layerId)) return;
      m.setPaintProperty(layerId, "line-width", AOI_STATIC_WIDTH);
      m.setPaintProperty(layerId, "line-opacity", AOI_STATIC_OPACITY);
      m.setPaintProperty(layerId, "line-blur", AOI_STATIC_BLUR);
    } catch {
      /* map torn down / style swapped mid-mutation - non-fatal */
    }
  };

  const tick = (now: number): void => {
    if (stopped) return;
    try {
      if (!m.getLayer(layerId)) {
        // The AOI box went away (case exit / clear) - end the loop cleanly.
        stop();
        return;
      }
      // Sine in [0,1]: 0 at trough, 1 at the bright peak.
      const phase = (now % GLOW_PERIOD_MS) / GLOW_PERIOD_MS;
      const wave = (1 - Math.cos(phase * 2 * Math.PI)) / 2;
      m.setPaintProperty(
        layerId,
        "line-width",
        GLOW_WIDTH_MIN + (GLOW_WIDTH_MAX - GLOW_WIDTH_MIN) * wave,
      );
      m.setPaintProperty(
        layerId,
        "line-opacity",
        GLOW_OPACITY_MIN + (GLOW_OPACITY_MAX - GLOW_OPACITY_MIN) * wave,
      );
      m.setPaintProperty(layerId, "line-blur", GLOW_BLUR_MAX * wave);
    } catch {
      /* mid-mutation race - skip this frame, keep looping */
    }
    if (hasRaf) rafId = requestAnimationFrame(tick);
  };

  function stop(): void {
    if (stopped) return;
    stopped = true;
    if (rafId !== null && hasRaf) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    setStatic();
  }

  if (hasRaf) {
    rafId = requestAnimationFrame(tick);
  }
  return { stop };
}

// --- thin MapLibre side-effect helpers ----------------------------------- //
//
// These are the ONLY map-touching functions. They are written defensively
// (try/catch, idempotent add/remove) so the toggle never throws into React.
// The shape they require is a structural subset of the maplibre-gl Map so the
// unit test can pass a tiny stub.

/** Structural subset of maplibre-gl Map used by the terrain helpers. */
export interface TerrainMapLike {
  getSource(id: string): unknown;
  addSource(id: string, spec: TerrainDemSourceSpec | Record<string, unknown>): void;
  removeSource(id: string): void;
  getLayer(id: string): unknown;
  addLayer(layer: Record<string, unknown>): void;
  removeLayer(id: string): void;
  setTerrain(spec: { source: string; exaggeration?: number } | null): void;
  setMaxPitch?(pitch: number): void;
  dragRotate?: { enable(): void; disable(): void };
  touchZoomRotate?: { enableRotation(): void; disableRotation(): void };
  touchPitch?: { enable(): void; disable(): void };
}

/**
 * Enable 3D terrain on the live map: add the terrain-RGB DEM source (if absent),
 * a hillshade layer, a sky layer, then `setTerrain` with exaggeration; finally
 * unlock two-finger pitch / rotate. Idempotent + defensive.
 *
 * `contoursRequested` is the persisted contour flag - for the first cut it only
 * logs a one-time TODO (see CONTOUR DECISION). Returns the source `origin` for
 * diagnostics / tests.
 */
export function applyTerrain3d(
  m: TerrainMapLike,
  opts: { demSource?: TerrainDemSourceSpec; contoursRequested?: boolean } = {},
): TerrainDemSourceSpec["origin"] | null {
  const dem = opts.demSource ?? buildTerrainDemSource();
  try {
    if (!m.getSource(TERRAIN_DEM_SOURCE_ID)) {
      m.addSource(TERRAIN_DEM_SOURCE_ID, dem);
    }
    // Hillshade layer over the DEM (subtle relief shading; placed first so the
    // basemap/overlays still read on top when terrain exaggeration is low).
    if (!m.getLayer(TERRAIN_HILLSHADE_LAYER_ID)) {
      m.addLayer({
        id: TERRAIN_HILLSHADE_LAYER_ID,
        type: "hillshade",
        source: TERRAIN_DEM_SOURCE_ID,
        paint: {
          "hillshade-exaggeration": 0.45,
          "hillshade-shadow-color": "#0a0e16",
        },
      });
    }
    // Sky layer (atmosphere) so the horizon reads as 3D once pitched. MapLibre
    // 4.x renders the `sky` layer type natively.
    if (!m.getLayer(TERRAIN_SKY_LAYER_ID)) {
      m.addLayer({
        id: TERRAIN_SKY_LAYER_ID,
        type: "sky",
        paint: {
          "sky-type": "atmosphere",
          "sky-atmosphere-sun-intensity": 5,
        },
      });
    }
    m.setTerrain({ source: TERRAIN_DEM_SOURCE_ID, exaggeration: TERRAIN_EXAGGERATION });

    // Unlock 3D navigation (the base map is locked 2D: maxPitch 0, no rotate).
    try {
      m.setMaxPitch?.(75);
      m.dragRotate?.enable();
      m.touchZoomRotate?.enableRotation();
      m.touchPitch?.enable();
    } catch {
      /* control toggles best-effort - terrain still renders without them */
    }

    if (opts.contoursRequested) {
      // CONTOUR STUB: real contour LINES need the `maplibre-contour` plugin
      // (not a dep). TODO(3d-terrain): add maplibre-contour, register a
      // mlcontour.DemSource over the same DEM, and add a `line` contour layer.
      // eslint-disable-next-line no-console
      console.info(
        "[terrain_3d] contours requested but maplibre-contour is not installed; " +
          "rendering terrain/hillshade only. TODO: wire maplibre-contour contour lines.",
      );
    }
    return dem.origin;
  } catch {
    // Any MapLibre throw (style mid-load, WebGL unavailable in a test env, etc.)
    // must not crash the toggle. Terrain just won't render this pass.
    return null;
  }
}

/**
 * Disable 3D terrain: setTerrain(null), drop the hillshade + sky layers + the
 * DEM source, and re-lock the camera to 2D (maxPitch 0, rotation disabled).
 * Idempotent + defensive - safe to call when terrain was never enabled.
 */
export function removeTerrain3d(m: TerrainMapLike): void {
  try {
    m.setTerrain(null);
  } catch {
    /* no terrain set - fine */
  }
  for (const id of [TERRAIN_HILLSHADE_LAYER_ID, TERRAIN_SKY_LAYER_ID]) {
    try {
      if (m.getLayer(id)) m.removeLayer(id);
    } catch {
      /* best-effort */
    }
  }
  try {
    if (m.getSource(TERRAIN_DEM_SOURCE_ID)) m.removeSource(TERRAIN_DEM_SOURCE_ID);
  } catch {
    /* best-effort */
  }
  // Re-lock to the flat 2D camera (Decision I: 2D-only navigation by default).
  try {
    m.touchPitch?.disable();
    m.touchZoomRotate?.disableRotation();
    m.dragRotate?.disable();
    m.setMaxPitch?.(0);
  } catch {
    /* best-effort */
  }
}
