// GRACE-2 web  -  LayerLegend (job-0065; interactive AOI-snapping keys, NATE
// overlay-layout spec 2026-06-17).
//
// Renders matplotlib-style colorbar "keys", one per continuous-raster layer that
// has a known style_preset. LEGEND v2 (NATE 2026-06-22): the key is ALWAYS a
// compact, FLAT two-row card (the old collapse/expand toggle is gone). Each key:
//   1. FLAT TWO-ROW LAYOUT - row 1 = title + hide(eye); row 2 = [min] gradient
//      bar [max] (horizontal: min at the LEFT end, max at the RIGHT end; vertical
//      when docked left/right: min at the BOTTOM, max at the TOP).
//   2. DRAGGABLE by the card BODY/EDGE (no dedicated grip icon); on release it
//      AUTO-SNAPS to the nearest VALID AOI side - LEFT, RIGHT, or TOP only.
//      BOTTOM is reserved for the sequence scrubber, so the legend never docks
//      there (legend_snap.nearestSide({ excludeBottom: true })).
//   3. DROP-ZONE SIGNALS - on drag-start, thin "area signal" affordances paint
//      along the valid snap targets (left/right/top edges of the AOI bbox) and
//      the nearest one highlights as the active target as the user drags toward
//      it; they clear on release/cancel (legend_snap.dropZoneSignals).
//   4. RESIZABLE per-key via a plain corner handle (width; height follows
//      content). SNAP-ORDERED counter-clockwise by stack order and STACKED
//      (offset outward) when keys share a side, so they never overlap.
//
// Positioning / data flow:
//   The component is rendered INSIDE the map container div (in Map.tsx) so it
//   anchors to the AOI box. Map.tsx passes:
//     - `layers`   : ordered layer list, top-of-stack first (LayerPanel order).
//     - `aoiRect`  : the TRUE projected AOI screen rectangle {left,top,right,
//                    bottom} (min/max over all four projected bbox corners). This
//                    is what the keys SNAP against  -  it carries the real AOI
//                    aspect ratio and on-screen skew, so the colorbar rails along
//                    the actual AOI edges, not a square-ish estimate.
//     - `anchor`   : the AOI bbox BOTTOM-edge midpoint {left, top} (projected)  - 
//                    used for the (already gap-nudged) vertical positioning the
//                    owner resolves; not the snap geometry.
//     - `barWidth` : the AOI bbox on-screen EAST-WEST extent in px (projected)  - 
//                    used to SIZE the default colorbar width.
//   Snap source of truth: when `aoiRect` is provided the keys snap CCW to ITS
//   four edges directly. When it is absent (off-screen / not yet projected) we
//   FALL BACK to reconstructing an approximate rect from `anchor` + `barWidth`
//   (legend_snap `rectFromAnchorAndWidth`  -  the bottom edge is exact, the height
//   is a square-ish estimate). When there is no AOI at all (no rect AND no
//   anchor/barWidth) the keys fall back to a static bottom-center stack so the
//   legend never vanishes.
//
// Invariant 1: this component displays received values only  -  no geography is
//   computed. minValue / maxValue / stops come from the preset registry (mirrors
//   the QML); the layer name comes from the ProjectLayerSummary wire. The
//   snap geometry is pure pixel math over the already-projected AOI rectangle.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ProjectLayerSummary } from "../contracts";
import { getStylePreset, StylePreset, type GradientStop } from "../lib/style-presets";
import {
  aoiScaleFactor,
  dropZoneSignals,
  layoutKeysToSides,
  nearestSide,
  rectFromAnchorAndWidth,
  sideForIndex,
  type AoiSide,
  type DropZoneSignal,
  type KeySize,
  type ScreenRect,
} from "../lib/legend_snap";
import { detectSequentialGroups } from "../LayerPanel";
import {
  getColormapStops,
  parseTitilerTileStyle,
  type ParsedRescale,
} from "../lib/titiler_colormap";
import { useIsMobile } from "../hooks/useIsMobile";
import { useAnimationState } from "../lib/use_animation_controller";

// JOB WEB-AOI-LEGEND (#157)  -  the collapsed "Show legend" pill must clear the
// mobile chat composer (the bottom-sheet at the foot of the screen). The pill
// is portaled to document.body with position:fixed, so on mobile we lift it
// above the composer by the device safe-area inset PLUS a fixed clearance that
// clears the collapsed sheet (drag handle + composer card + the sheet's own
// SHEET_BOTTOM_OFFSET lift). On desktop the chat is a right-side panel, not a
// bottom sheet, so the pill keeps its original low bottom-center position.
export const MOBILE_LEGEND_PILL_CLEARANCE_PX = 96;
export const MOBILE_LEGEND_PILL_BOTTOM_CSS = `calc(env(safe-area-inset-bottom) + ${MOBILE_LEGEND_PILL_CLEARANCE_PX}px)`;
export const DESKTOP_LEGEND_PILL_BOTTOM_PX = 24;

// Item a (Z-HIERARCHY, NATE 2026-06-20)  -  the legend must render BEHIND the chat
// (z=32) and the Layers/Cases panels (z=20) and the desktop hamburgers (z=30),
// but ABOVE the map. A single low z keeps the legend in the map's chrome layer
// so a user can always reach the chat + layers controls over it. (Previously the
// keys used z=50, which painted OVER the chat + panels  -  the reported bug.)
// On mobile the Layers drawer (z=40/41) is a transient OVERLAY; the legend
// staying at z=15 means it sits behind the open drawer, which is correct (the
// drawer is the focused surface). The mobile show/hide toggle moves INTO the
// drawer's expanded Layers section (item b) so it is never lost behind the chat.
export const LEGEND_Z_INDEX = 15;

export interface LayerLegendProps {
  /** Ordered layer list, top-of-stack first (same order as LayerPanel). */
  layers: ProjectLayerSummary[];
  /**
   * EDGE-RAIL snap (NATE 2026-06-17)  -  the TRUE projected AOI screen rectangle
   * {left, top, right, bottom} in absolute map-container coords. The owner
   * (Map.tsx) projects ALL FOUR bbox corners each move/zoom/render and passes
   * their min/max box here (computeBboxScreenRect). When present this is the
   * snap source of truth: the keys rail CCW along ITS four edges, so the snap
   * follows the real AOI aspect ratio + on-screen skew (not a square estimate).
   * Null/undefined => no true rect (off-screen / not yet projected) => the keys
   * fall back to reconstructing an approximate rect from `anchor` + `barWidth`.
   */
  aoiRect?: ScreenRect | null;
  /**
   * job-0321 (F43)  -  optional screen-space anchor: the AOI bbox BOTTOM-edge
   * midpoint {left, top} (absolute, map-container coords). The owner (Map.tsx)
   * projects it each move/zoom/render. Used as the FALLBACK snap-rect source
   * (with `barWidth`, via rectFromAnchorAndWidth) only when `aoiRect` is absent.
   * Null/undefined AND no `aoiRect` => no AOI on screen => the keys fall back to
   * a static bottom-center stack so they never vanish.
   */
  anchor?: { left: number; top: number } | null;
  /**
   * FIX 4 (NATE 2026-06-17)  -  the AOI bbox's ON-SCREEN east-west extent in px
   * (already clamped by Map.tsx). Used to SIZE the default colorbar width, and
   * (with `anchor`) to reconstruct the FALLBACK AOI rectangle for snapping when
   * `aoiRect` is absent. Null => no AOI bbox => static fallback width +
   * bottom-center stack.
   */
  barWidth?: number | null;
  /**
   * Item f (NATE 2026-06-20)  -  reserve vertical px below the AOI bottom edge so
   * the bottom-side keys clear the SCRUBBER (which pins bottom-center of the AOI
   * box). When > 0 the bottom-side keys are pushed down past the scrubber's
   * footprint so the legend is never obscured by it. 0 / undefined => no reserve.
   */
  bottomReservePx?: number | null;
  /**
   * Item b (NATE 2026-06-20)  -  CONTROLLED hidden state. When provided the
   * parent owns whether the legend is shown (the toggle lives in the Layers
   * panel on mobile). When omitted the legend keeps its own internal hidden
   * state (desktop default). Pair with `onHiddenChange`.
   */
  hidden?: boolean;
  /** Item b  -  fired when the user toggles hide/show (controlled mode). */
  onHiddenChange?: (hidden: boolean) => void;
  /**
   * Item b  -  suppress the floating "Show legend" pill entirely. On mobile the
   * show/hide affordance lives INSIDE the expanded Layers section (out of the
   * way of the chat composer), so the floating pill must not also render. The
   * keys themselves still render when not hidden.
   */
  suppressShowPill?: boolean;
}

/**
 * Item e (NATE 2026-06-20)  -  the SERIES IDENTITY of a raster layer: the colormap
 * + scale it paints with. Per-frame depth COGs ("Flood depth step N") AND the
 * max/peak depth layer all share the SAME colormap + rescale, so they form ONE
 * series and must collapse to ONE legend key (not one-per-frame + a peak key).
 *
 * The key is the TiTiler colormap_name + rescale (the SOURCE OF TRUTH for what
 * the map paints) when present  -  this is what the depth frames + the peak depth
 * layer all carry, so they share ONE key. When a layer carries NO TiTiler
 * colormap on its URL (a plain QGIS-WMS / preset-only single raster, with no
 * frame-truth scale), it is NOT part of a TiTiler series, so we key it by its
 * own layer_id (one key per such layer  -  the prior behavior). This keeps
 * distinct preset-only rasters each legible while folding the genuine
 * same-colormap depth series into a single key (item e).
 */
function seriesKeyFor(
  layer: ProjectLayerSummary,
  style: { rescale: ParsedRescale | null; colormapName: string | null },
): string {
  if (style.colormapName) {
    const r = style.rescale ? `${style.rescale.min},${style.rescale.max}` : "";
    return `cmap:${style.colormapName}|rescale:${r}`;
  }
  // No URL colormap -> not a TiTiler series; key per-layer so each distinct
  // preset-only raster keeps its own legend key.
  return `layer:${layer.layer_id}`;
}

/** A raster layer that resolved to a known preset  -  one legend key per entry. */
interface LegendKeyModel {
  layerId: string;
  preset: StylePreset;
  /**
   * FRAME-TRUTH (NATE 2026-06-19)  -  the rescale + colormap parsed from the
   * layer's TiTiler tile-template URL, when present. This is the SOURCE OF
   * TRUTH: when set, the key renders these bounds/colors (what the map actually
   * paints) instead of the preset guess. Null when the URL carries no such
   * params (QGIS WMS / non-animated single raster) => preset fallback.
   */
  rescale: ParsedRescale | null;
  /** Parsed-colormap CSS gradient stops (from `colormap_name`), or null. */
  colormapStops: GradientStop[] | null;
}

/** Per-key interactive UI state the user can drive (width + free pos + snap). */
interface KeyUiState {
  /** User-chosen width override (px). Undefined => default snapped width. */
  width?: number;
  /** While dragging, the free top-left screen position (overrides the snap). */
  free?: { left: number; top: number } | null;
  /**
   * SIDE-SNAP (NATE 2026-06-22) - the AOI side this key was dragged to on the
   * last release. When set it OVERRIDES the key's CCW index-assigned side, so
   * dragging a key to (say) the right edge SNAPS it there AND flips its
   * orientation to vertical (left/right -> vertical, top/bottom -> horizontal).
   * Undefined => the key keeps its default CCW side. Cleared is not needed; a
   * later drag overwrites it. Only meaningful when an AOI rect is present.
   */
  sideOverride?: AoiSide;
}

/** Builds a CSS linear-gradient string from gradient stops (sorted by caller). */
function buildGradient(stops: GradientStop[]): string {
  const parts = stops
    .map((s) => `${s.color} ${(s.position * 100).toFixed(2)}%`)
    .join(", ");
  return `linear-gradient(to right, ${parts})`;
}

/**
 * FRAME-TRUTH (NATE 2026-06-19)  -  parses the TiTiler rescale + colormap out of a
 * layer's tile-template URL. The AWS frame layers carry the truth (rescale +
 * colormap_name) as query params on the XYZ template. We check `wms_url` first
 * (the field Map.tsx registers the tile source from  -  it holds the `{z}`
 * template for TiTiler layers) and fall back to `uri`. Returns null fields when
 * neither carries the params (QGIS WMS / non-animated single raster), so the
 * caller keeps the style_preset behavior. Never throws.
 */
function parseLayerTitilerStyle(layer: ProjectLayerSummary): {
  rescale: ParsedRescale | null;
  colormapStops: GradientStop[] | null;
  colormapName: string | null;
} {
  const fromWms = parseTitilerTileStyle(layer.wms_url);
  const fromUri = parseTitilerTileStyle(layer.uri);
  // Prefer whichever field actually carried each param (wms_url first).
  const rescale = fromWms.rescale ?? fromUri.rescale;
  const colormapName = fromWms.colormapName ?? fromUri.colormapName;
  return {
    rescale,
    colormapStops: getColormapStops(colormapName),
    colormapName: colormapName ?? null,
  };
}

// Default colorbar width when there is no AOI bbox to size against.
const STATIC_LEGEND_WIDTH = 320;
// Min/max width a user may resize a key to.
const KEY_MIN_WIDTH = 140;
const KEY_MAX_WIDTH = 520;
// Estimated key heights for the snap layout. These only feed the stacking math
// (so keys don't overlap); the rendered card sizes itself. LEGEND v2: the key is
// always the FLAT two-row card (title row + value/bar row), so there is a single
// horizontal-dock height; vertical-docked (left/right) keys are taller (a tall
// bar) so we feed a separate vertical height to the stacking math.
const KEY_HEIGHT_FLAT = 56;
const KEY_HEIGHT_VERTICAL = 150;
// Horizontal gap between keys when falling back to the bottom-center stack.
const FALLBACK_STACK_GAP = 10;

/**
 * Selects one legend key per eligible raster layer, in stack order
 * (top-of-stack first).
 *
 * SEQUENTIAL-GROUP DEDUP (item 1): layers that belong to a sequential group
 * (enumerated temporal stack) all share the same colormap / preset. Rendering
 * one key per frame would crowd the screen with N identical bars. Instead we
 * detect groups here and emit exactly ONE key per group (using the active /
 * first member's preset). Non-grouped raster layers each still get their own
 * key.
 *
 * SERIES DEDUP (item e, NATE 2026-06-20): beyond sequential groups, ANY two
 * layers that share the SAME series identity (colormap + rescale, see
 * seriesKeyFor) collapse to ONE key. This folds the max/PEAK depth layer into
 * the same series as the per-frame depth COGs  -  they all paint with the same
 * colormap + scale, so they read off one legend, not one-per-frame + a peak.
 * The FIRST eligible layer (group or standalone) to claim a series key wins;
 * later layers with the same series key are skipped.
 */
function selectKeyModels(layers: ProjectLayerSummary[]): LegendKeyModel[] {
  // Detect sequential groups to emit one key per group.
  const groups = detectSequentialGroups(layers);
  // Collect layer_ids that belong to a group; track which groups we've emitted.
  const groupedIds = new Set<string>();
  const emittedGroupKeys = new Set<string>();
  for (const g of groups) {
    for (const l of g.layers) groupedIds.add(l.layer_id);
  }

  // Item e  -  every series identity already emitted (by a group OR a standalone
  // layer). A later layer sharing one of these is the same colormap/scale, so it
  // dedups into the existing key rather than spawning a duplicate.
  const emittedSeries = new Set<string>();

  const out: LegendKeyModel[] = [];
  for (const l of layers) {
    if (l.layer_type !== "raster") continue;
    if (l.style_preset == null) continue;
    const preset = getStylePreset(l.style_preset);
    if (!preset) continue;

    if (groupedIds.has(l.layer_id)) {
      // Find the group this layer belongs to and emit one key for that group.
      const g = groups.find((gr) => gr.layers.some((m) => m.layer_id === l.layer_id));
      if (!g || emittedGroupKeys.has(g.key)) continue; // already emitted or no group
      emittedGroupKeys.add(g.key);
      // Use the first member of the group as the key representative (they all
      // share the same preset / colormap / rescale). layer_id keys the UI state.
      // FRAME-TRUTH: all frames share the same rescale+colormap, so parse them
      // from the representative frame's tile URL (item 4).
      const rep = g.layers[0];
      if (!rep) continue;
      const repPreset = getStylePreset(rep.style_preset ?? "");
      const repStyle = parseLayerTitilerStyle(rep);
      // Item e  -  register the group's series so a standalone peak/max layer with
      // the same colormap + rescale folds INTO this key instead of adding its own.
      const repSeries = seriesKeyFor(rep, {
        rescale: repStyle.rescale,
        colormapName: repStyle.colormapName,
      });
      if (emittedSeries.has(repSeries)) continue;
      emittedSeries.add(repSeries);
      out.push({
        layerId: `group:${g.key}`,
        // Fallback to the current layer's preset if the rep doesn't resolve.
        preset: repPreset ?? preset,
        rescale: repStyle.rescale,
        colormapStops: repStyle.colormapStops,
      });
    } else {
      const style = parseLayerTitilerStyle(l);
      // Item e  -  one key per SERIES. A standalone layer sharing a series with an
      // already-emitted group/layer (e.g. the peak depth alongside depth frames)
      // dedups into that existing key.
      const series = seriesKeyFor(l, {
        rescale: style.rescale,
        colormapName: style.colormapName,
      });
      if (emittedSeries.has(series)) continue;
      emittedSeries.add(series);
      out.push({
        layerId: l.layer_id,
        preset,
        rescale: style.rescale,
        colormapStops: style.colormapStops,
      });
    }
  }
  return out;
}

/**
 * Item b/e (NATE 2026-06-20)  -  does the legend have ANY content for these
 * layers? Exported so the Layers panel can decide whether to render the mobile
 * "show/hide legend" toggle (only when there's a legend to toggle).
 */
export function legendHasContent(layers: ProjectLayerSummary[]): boolean {
  return selectKeyModels(layers).length > 0;
}

export function LayerLegend({
  layers,
  aoiRect: trueRect,
  anchor,
  barWidth,
  bottomReservePx,
  hidden: hiddenProp,
  onHiddenChange,
  suppressShowPill,
}: LayerLegendProps): JSX.Element | null {
  // One key per eligible raster layer, in stack order.
  const keyModels = useMemo(() => selectKeyModels(layers), [layers]);

  // JOB WEB-AOI-LEGEND (#157)  -  lift the collapsed "Show legend" pill above the
  // mobile chat composer so it does not overlap the bottom-sheet input form.
  const isMobile = useIsMobile();

  // Item f  -  is the SCRUBBER currently showing? The scrubber pins bottom-center
  // of the AOI box (just below its bottom edge), exactly where the legend's
  // bottom-side key would otherwise sit. The scrubber renders whenever the
  // shared AnimationController has an active group, so we read that here to
  // push the legend's bottom-side keys past the scrubber's footprint (the
  // explicit `bottomReservePx` prop, when supplied, overrides this default).
  const anim = useAnimationState();
  const scrubberActive = anim.activeGroupKey != null;

  // Per-key interactive state, keyed by layer_id so it survives reorders.
  const [uiState, setUiState] = useState<Record<string, KeyUiState>>({});
  // Whether the whole legend is hidden (the eye toggle on the first key).
  // Item b  -  CONTROLLED when `hidden` is supplied (the parent owns it so the
  // toggle can live in the Layers panel on mobile); else internal state.
  const [hiddenInternal, setHiddenInternal] = useState(false);
  const isControlled = hiddenProp !== undefined;
  const hidden = isControlled ? !!hiddenProp : hiddenInternal;
  const setHidden = useCallback(
    (next: boolean) => {
      if (!isControlled) setHiddenInternal(next);
      onHiddenChange?.(next);
    },
    [isControlled, onHiddenChange],
  );

  // Live drag bookkeeping. Tracks the key being dragged, the pointer offset
  // inside the card, the card size (so release can compute its CENTER for
  // nearest-side snapping), and the latest free top-left. Stored in a ref so the
  // window listeners read fresh values without re-binding each render.
  const dragRef = useRef<{
    layerId: string;
    offsetX: number;
    offsetY: number;
    width: number;
    height: number;
    last: { left: number; top: number };
  } | null>(null);

  // LEGEND v2 (DROP-ZONE SIGNALS, NATE 2026-06-22)  -  while a key is being
  // dragged we paint thin "area signal" affordances along the VALID snap targets
  // (left/right/top edges of the AOI; never bottom) and HIGHLIGHT the nearest one
  // as the live active target. `dragActive` is the layerId mid-drag (null when
  // idle), `activeDropSide` the side currently nearest the dragged card. Both are
  // React state (not just the drag ref) so the signals re-render as the drag
  // moves and clear on release/cancel.
  const [dragActive, setDragActive] = useState<string | null>(null);
  const [activeDropSide, setActiveDropSide] = useState<AoiSide | null>(null);

  // The AOI rectangle in screen space that the keys SNAP against. Prefer the
  // TRUE projected rect (all four bbox corners, min/max box) threaded from
  // Map.tsx  -  it carries the real AOI aspect ratio + on-screen skew, so the
  // CCW edge-rail follows the actual AOI edges. Only when the true rect is
  // absent (off-screen / not yet projected) do we fall back to reconstructing
  // an APPROXIMATE rect from anchor + barWidth (square-ish height estimate).
  // Null from both => no AOI on screen => bottom-center stack fallback.
  const aoiRect: ScreenRect | null = useMemo(
    () => trueRect ?? rectFromAnchorAndWidth(anchor, barWidth),
    [trueRect, anchor, barWidth],
  );
  // SIDE-SNAP (NATE 2026-06-22) - mirror the snap rect into a ref so the window
  // pointer-up handler (endDrag, a stable useCallback) can read the CURRENT rect
  // to compute the nearest AOI side on release without re-binding per render.
  const aoiRectRef = useRef<ScreenRect | null>(aoiRect);
  aoiRectRef.current = aoiRect;

  // Item d (SCALE WITH AOI, NATE 2026-06-20)  -  the legend chrome (font, padding,
  // bar height) scales with the AOI's on-screen size so a zoomed-out tiny bbox
  // gets a proportionally small legend (not a fixed-px one that dwarfs it) and a
  // zoomed-in big bbox gets a larger one  -  both clamped to [min, max] so the
  // legend is never unusably tiny or absurdly huge. Recomputes whenever the rect
  // changes (Map.tsx re-projects on every move/zoom and re-threads aoiRect).
  const scale = useMemo(() => aoiScaleFactor(aoiRect), [aoiRect]);

  // Default per-key width: the AOI on-screen width (clamped) when available,
  // else the static fallback (also scaled). A user resize overrides this per key.
  const defaultWidth = useMemo(() => {
    const w =
      typeof barWidth === "number" && Number.isFinite(barWidth) && barWidth > 0
        ? barWidth
        : STATIC_LEGEND_WIDTH * scale;
    return Math.max(KEY_MIN_WIDTH, Math.min(w, KEY_MAX_WIDTH));
  }, [barWidth, scale]);

  const widthFor = useCallback(
    (layerId: string): number => {
      const override = uiState[layerId]?.width;
      if (typeof override === "number" && override > 0) {
        return Math.max(KEY_MIN_WIDTH, Math.min(override, KEY_MAX_WIDTH));
      }
      return defaultWidth;
    },
    [uiState, defaultWidth],
  );

  // ITEM 5 (NATE 2026-06-22)  -  when the SCRUBBER is showing, the bottom-center
  // band is occupied by it, so START the CCW key layout on the RIGHT side (offset
  // +1 in the bottom->right->top->left order). The first key then rails VERTICALLY
  // down the right edge of the bbox (orientation follows the side, below), and
  // the legend + scrubber never collide. When NO scrubber is shown the offset is
  // 0 (the canonical bottom-first placement is unchanged).
  const sideStartOffset = scrubberActive ? 1 : 0;

  // SIDE-SNAP (NATE 2026-06-22) - the side each key docks on: the user's
  // drag-snap override when present (set by endDrag via nearestSide), else the
  // canonical CCW index side (with the scrubber start offset). This single array
  // is the source of truth for BOTH the snapped position AND the orientation /
  // side label below, so a dragged key snaps to its side with the matching
  // orientation. Keyed off keyModels so it stays in lockstep with the rendered keys.
  const resolvedSides: AoiSide[] = useMemo(
    () =>
      keyModels.map(
        (k, idx) =>
          uiState[k.layerId]?.sideOverride ??
          sideForIndex(idx + sideStartOffset),
      ),
    [keyModels, uiState, sideStartOffset],
  );

  // LEGEND v2  -  the orientation a key dock-side implies: left/right -> vertical
  // (a tall bar), top/bottom -> horizontal. Used for BOTH the stacking-height
  // math (a vertical key consumes a taller footprint) and the rendered bar.
  const orientationForSide = useCallback(
    (side: AoiSide): "vertical" | "horizontal" =>
      side === "left" || side === "right" ? "vertical" : "horizontal",
    [],
  );

  // Compute the snapped position for every key. When an AOI rect is present we
  // lay keys out CCW (bottom, right, top, left, stacking on repeat). With no AOI
  // we stack them along a static bottom-center row. A key being actively dragged
  // uses its `free` position instead of the snapped one. LEGEND v2: the key is
  // always the FLAT two-row card, so the stacking HEIGHT is the flat height for
  // a horizontal dock and the taller vertical-bar height for a left/right dock.
  const sizes: KeySize[] = useMemo(
    () =>
      keyModels.map((k, idx) => {
        const side = resolvedSides[idx] ?? sideForIndex(idx + sideStartOffset);
        const height =
          orientationForSide(side) === "vertical"
            ? KEY_HEIGHT_VERTICAL
            : KEY_HEIGHT_FLAT;
        return { width: widthFor(k.layerId), height };
      }),
    [keyModels, widthFor, resolvedSides, sideStartOffset, orientationForSide],
  );

  // Item f  -  extra px to push bottom-side keys past the scrubber's footprint
  // (the scrubber pins bottom-center of the AOI box). The explicit prop wins;
  // otherwise default to a sensible reserve WHENEVER the scrubber is active so
  // the legend is never obscured by it. 0 when neither applies.
  const SCRUBBER_FOOTPRINT_PX = 52; // scrubber height (~40) + its 12px gap.
  const bottomReserve =
    typeof bottomReservePx === "number" && bottomReservePx > 0
      ? bottomReservePx
      : scrubberActive
        ? SCRUBBER_FOOTPRINT_PX
        : 0;

  const snapped = useMemo(() => {
    if (aoiRect) {
      // Lay each key against its RESOLVED side (override or CCW), stacking keys
      // that share a side so they never overlap.
      const base = layoutKeysToSides(aoiRect, sizes, resolvedSides);
      // Item f  -  shove any bottom-side keys down past the scrubber so the legend
      // is never obscured by it (the scrubber sits just below the AOI bottom
      // edge). Top/right/left keys are untouched. With sideStartOffset=1 the
      // first 3 keys avoid the bottom entirely; this still guards a 4th+ key.
      if (bottomReserve <= 0) return base;
      return base.map((r) =>
        r.side === "bottom" ? { ...r, top: r.top + bottomReserve } : r,
      );
    }
    // No AOI: lay the keys out as a bottom-center row (each key centered, then
    // stacked upward so they don't overlap). We synthesize a degenerate rect at
    // a nominal bottom-center point; this keeps the legend visible.
    let consumed = 0;
    return sizes.map((s) => {
      const top = -(FALLBACK_STACK_GAP + consumed + s.height);
      consumed += s.height + FALLBACK_STACK_GAP;
      return { left: -s.width / 2, top, side: "bottom" as AoiSide };
    });
  }, [aoiRect, sizes, bottomReserve, resolvedSides]);

  // --- drag wiring --------------------------------------------------------- //

  // LEGEND v2  -  the dropped card's CENTER mapped to the nearest VALID snap side
  // (left/right/top; BOTTOM excluded - it is reserved for the scrubber). Returns
  // undefined when there is no AOI rect (off-screen) so the caller leaves the
  // AOI-less bottom-center fallback in place. Shared by the live drag (to drive
  // the active drop-zone highlight) and the release (to set the side override).
  const nearestLegendSide = useCallback(
    (centerLeft: number, centerTop: number, w: number, h: number): AoiSide | undefined => {
      const rect = aoiRectRef.current;
      if (!rect) return undefined;
      return nearestSide(
        rect,
        { x: centerLeft + w / 2, y: centerTop + h / 2 },
        { excludeBottom: true },
      );
    },
    [],
  );

  const endDrag = useCallback(() => {
    const drag = dragRef.current;
    dragRef.current = null;
    // Clear the drop-zone signals + active highlight on release/cancel.
    setDragActive(null);
    setActiveDropSide(null);
    if (!drag) return;
    const layerId = drag.layerId;
    // SIDE-SNAP / LEGEND v2 - on release the key SNAPS to the VALID AOI side
    // NEAREST where it was dropped (LEFT/RIGHT/TOP only - bottom is reserved for
    // the scrubber). We take the dropped card's CENTER and ask legend_snap's
    // bottom-excluded nearestSide which edge it is closest to, then store that as
    // a per-key sideOverride. The layout + orientation + side label all honor the
    // override, so dragging a key toward the right snaps it there AND flips it to
    // a vertical bar, and a drag toward the BOTTOM snaps to the nearest of
    // left/right/top instead. With NO AOI rect (off-screen) there is no side to
    // snap to, so we just clear `free` and the bottom-center fallback holds.
    const snappedSide = nearestLegendSide(
      drag.last.left,
      drag.last.top,
      drag.width,
      drag.height,
    );
    setUiState((prev) => {
      const next = { ...prev };
      const cur = next[layerId] ?? {};
      next[layerId] = {
        ...cur,
        free: null,
        // Only record an override when we actually resolved a side (AOI present).
        ...(snappedSide ? { sideOverride: snappedSide } : {}),
      };
      return next;
    });
  }, [nearestLegendSide]);

  const onPointerMoveWindow = useCallback(
    (ev: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const left = ev.clientX - drag.offsetX;
      const top = ev.clientY - drag.offsetY;
      // Track the latest free top-left on the drag ref so release can compute the
      // card center for nearest-side snapping (the ref read is fresh; state is not).
      drag.last = { left, top };
      // LEGEND v2  -  live-highlight the drop-zone signal nearest the card center
      // so the user sees which side it will snap to (left/right/top only).
      const side = nearestLegendSide(left, top, drag.width, drag.height);
      setActiveDropSide(side ?? null);
      setUiState((prev) => {
        const next = { ...prev };
        const cur = next[drag.layerId] ?? {};
        next[drag.layerId] = { ...cur, free: { left, top } };
        return next;
      });
    },
    [nearestLegendSide],
  );

  // Bind window listeners once; they read the live ref so no re-bind per drag.
  useEffect(() => {
    const move = (e: PointerEvent) => onPointerMoveWindow(e);
    const up = () => endDrag();
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, [onPointerMoveWindow, endDrag]);

  const startDrag = useCallback(
    (layerId: string, ev: React.PointerEvent<HTMLElement>) => {
      // EDGE/BODY-GRAB (NATE 2026-06-22) - the whole legend card body IS the drag
      // handle (there is no separate grip icon - that was visual clutter NATE
      // asked to drop); a pointer-down anywhere on the chrome starts the drag.
      // We only EXCLUDE the interactive controls (the hide button) and the resize
      // handle, all tagged data-legend-no-drag, so a click on those does its own
      // thing instead of dragging.
      const target = ev.target as HTMLElement;
      if (target.closest("[data-legend-no-drag]")) return;
      const card = ev.currentTarget.getBoundingClientRect();
      dragRef.current = {
        layerId,
        offsetX: ev.clientX - card.left,
        offsetY: ev.clientY - card.top,
        width: card.width,
        height: card.height,
        last: { left: card.left, top: card.top },
      };
      // LEGEND v2  -  arm the drop-zone signals for this drag and seed the active
      // side from the card's current center, so the targets appear immediately.
      setDragActive(layerId);
      setActiveDropSide(
        nearestLegendSide(card.left, card.top, card.width, card.height) ?? null,
      );
      // Seed a free position at the current spot so the first move is smooth.
      setUiState((prev) => {
        const next = { ...prev };
        const cur = next[layerId] ?? {};
        next[layerId] = {
          ...cur,
          free: { left: card.left, top: card.top },
        };
        return next;
      });
    },
    [nearestLegendSide],
  );

  // --- resize wiring ------------------------------------------------------- //

  const resizeRef = useRef<{
    layerId: string;
    startX: number;
    startWidth: number;
  } | null>(null);

  const onResizeMove = useCallback((ev: PointerEvent) => {
    const r = resizeRef.current;
    if (!r) return;
    const delta = ev.clientX - r.startX;
    const w = Math.max(KEY_MIN_WIDTH, Math.min(r.startWidth + delta, KEY_MAX_WIDTH));
    setUiState((prev) => {
      const next = { ...prev };
      const cur = next[r.layerId] ?? {};
      next[r.layerId] = { ...cur, width: w };
      return next;
    });
  }, []);

  const endResize = useCallback(() => {
    resizeRef.current = null;
  }, []);

  useEffect(() => {
    const move = (e: PointerEvent) => onResizeMove(e);
    const up = () => endResize();
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, [onResizeMove, endResize]);

  const startResize = useCallback(
    (layerId: string, ev: React.PointerEvent<HTMLElement>) => {
      ev.stopPropagation();
      ev.preventDefault();
      resizeRef.current = {
        layerId,
        startX: ev.clientX,
        startWidth: widthFor(layerId),
      };
    },
    [widthFor],
  );

  // Nothing eligible => render nothing (preserves the old hide contract).
  if (keyModels.length === 0) return null;

  // LEGEND v2 (DROP-ZONE SIGNALS)  -  while a key is being dragged, paint a thin
  // "area signal" along each VALID snap target (left/right/top edges of the AOI;
  // never bottom) and highlight the one nearest the dragged card. Computed here
  // (after the hidden/empty guards) only when a drag is in flight AND an AOI rect
  // exists; cleared automatically on release/cancel (dragActive -> null).
  const dropSignals: DropZoneSignal[] =
    dragActive && aoiRect
      ? dropZoneSignals(aoiRect, { activeSide: activeDropSide })
      : [];

  // When fully hidden, render only a tiny "show legend" pill (bottom-center).
  // Portal to document.body so it appears above the mobile chat panel.
  //
  // Item b  -  when `suppressShowPill` is set the floating pill is NOT rendered:
  // the show/hide affordance lives inside the expanded Layers section instead
  // (the parent renders <MobileLegendToggle/>), so the pill must not also float
  // over the chat. We render nothing in that case (the parent owns re-showing).
  if (hidden) {
    if (suppressShowPill) return null;
    return createPortal(
      <button
        type="button"
        data-testid="grace2-layer-legend-show"
        onClick={() => setHidden(false)}
        style={{
          position: "fixed",
          // JOB WEB-AOI-LEGEND (#157)  -  on mobile, sit ABOVE the chat composer
          // (safe-area inset + clearance for the collapsed sheet); on desktop
          // keep the original low bottom-center position (no bottom sheet).
          bottom: isMobile
            ? MOBILE_LEGEND_PILL_BOTTOM_CSS
            : DESKTOP_LEGEND_PILL_BOTTOM_PX,
          left: "50%",
          transform: "translateX(-50%)",
          padding: "5px 12px",
          background: "rgba(17,18,23,0.78)",
          backdropFilter: "blur(6px)",
          WebkitBackdropFilter: "blur(6px)",
          border: "1px solid rgba(255,255,255,0.10)",
          borderRadius: 999,
          color: "#ddd",
          fontFamily: "system-ui, sans-serif",
          fontSize: 11,
          fontWeight: 600,
          cursor: "pointer",
          pointerEvents: "auto",
          // Item a  -  BELOW the chat (z=32) + panels (z=20); the pill is part of
          // the legend's map-chrome layer, never over the chat/layers controls.
          zIndex: LEGEND_Z_INDEX,
        }}
      >
        Show legend
      </button>,
      document.body,
    );
  }

  // The wrapper keeps a stable testid so existing tests + Map.tsx mounting
  // expectations hold. It is a zero-size placeholder; the actual key cards
  // portal to document.body with position:fixed so they escape the map
  // container's stacking context and appear above the mobile chat panel
  // (item 6 fix: z-index 50 > drawer z-index 30-41).
  //
  // position:fixed keys use the SAME snapped coordinates as before because
  // the map container is position:absolute;inset:0 relative to the app shell
  // which is position:fixed;inset:0  -  so map-container coords == viewport coords.
  return (
    <div
      data-testid="grace2-layer-legend"
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        // Zero z-index on the anchor wrapper  -  the actual keys are portaled.
        zIndex: 0,
      }}
    >
      {keyModels.map((model, idx) => {
        const layerId = model.layerId;
        const preset = model.preset;
        const ui = uiState[layerId] ?? {};
        const width = widthFor(layerId);
        // `snapped` is built 1:1 from `keyModels`, so this is always defined;
        // the fallback satisfies noUncheckedIndexedAccess.
        const snapPos = snapped[idx] ?? { left: 0, top: 0, side: "bottom" as AoiSide };

        // Position: free (dragging) > snapped (AOI) > fallback bottom-center.
        // Keys use position:fixed (portaled to document.body) so coords map
        // 1:1 to viewport space (map container is inset:0 -> same origin).
        let posStyle: React.CSSProperties;
        if (ui.free) {
          posStyle = { left: ui.free.left, top: ui.free.top };
        } else if (aoiRect) {
          posStyle = { left: snapPos.left, top: snapPos.top };
        } else {
          // Fallback: snapPos.left/top are offsets from bottom-center; realize
          // them with left:50% + a translate so the row sits bottom-center.
          posStyle = {
            left: "50%",
            bottom: 24,
            transform: `translate(calc(-50% + ${snapPos.left + width / 2}px), ${snapPos.top}px)`,
          };
        }

        // FRAME-TRUTH (NATE 2026-06-19)  -  the gradient + numeric bounds match
        // what the map actually paints. The parsed-from-URL colormap/rescale are
        // the SOURCE OF TRUTH when present; the style_preset is the FALLBACK.
        const minLabel = model.rescale ? model.rescale.min : preset.minValue;
        const maxLabel = model.rescale ? model.rescale.max : preset.maxValue;
        // The preset unit is meaningful only for the preset's own scale; when
        // the bounds come from the URL rescale (an arbitrary layer), drop the
        // unit so we never mislabel (e.g. tagging a temperature ramp with "m").
        const unitLabel = model.rescale ? "" : preset.unit;
        // SIDE-SNAP / ITEM 5  -  the side label MUST match the snapped layout: it
        // reads from the SAME resolvedSides array (the user's drag-snap override
        // when present, else the CCW index side incl. the scrubber-active start
        // offset). So a key dragged to the right reads as a vertical RIGHT bar.
        // AOI-less fallback stays bottom-horizontal.
        const sideLabel: AoiSide = aoiRect
          ? resolvedSides[idx] ?? sideForIndex(idx + sideStartOffset)
          : "bottom";

        // Item g (ORIENTATION, NATE 2026-06-20)  -  the colorbar is VERTICAL (a
        // tall bar) when the key docks on the LEFT or RIGHT side of the AOI, and
        // HORIZONTAL when it docks on TOP or BOTTOM (and in the AOI-less
        // bottom-center fallback). The gradient direction follows: bottom->top
        // for vertical (min at the bottom, max at the top), left->right for
        // horizontal (min at the left, max at the right).
        const orientation: "vertical" | "horizontal" =
          sideLabel === "left" || sideLabel === "right" ? "vertical" : "horizontal";
        const stops = model.colormapStops ?? preset.stops;
        const gradient =
          orientation === "vertical"
            ? `linear-gradient(to top, ${stops
                .map((s) => `${s.color} ${(s.position * 100).toFixed(2)}%`)
                .join(", ")})`
            : buildGradient(stops);

        // Item d  -  scaled type + chrome metrics (clamped via the scale factor).
        // LEGEND v2: a single FLAT key (no compact branch), so one metric set.
        const titleFont = Math.round(11 * scale);
        const labelFont = Math.round(10 * scale);
        const barThickness = Math.round(12 * scale);
        // A vertical bar needs a sensible height to read as a tall colorbar.
        const verticalBarHeight = Math.round(120 * scale);

        const minText = `${minLabel}${unitLabel ? ` ${unitLabel}` : ""}`;
        const maxText = `${maxLabel}${unitLabel ? ` ${unitLabel}` : ""}`;

        const keyCard = (
          <div
            key={layerId}
            data-testid="grace2-layer-legend-key"
            data-legend-side={sideLabel}
            data-legend-orientation={orientation}
            onPointerDown={(e) => startDrag(layerId, e)}
            style={{
              position: "fixed",
              ...posStyle,
              width,
              padding: "7px 10px 8px",
              background: "rgba(17,18,23,0.78)",
              backdropFilter: "blur(6px)",
              WebkitBackdropFilter: "blur(6px)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: 10,
              boxShadow: "0 2px 12px rgba(0,0,0,0.45)",
              fontFamily: "system-ui, sans-serif",
              color: "#eee",
              pointerEvents: "auto",
              cursor: "grab",
              userSelect: "none",
              touchAction: "none",
              // Item a  -  BELOW the chat (z=32) + Layers/Cases panels (z=20) +
              // hamburgers (z=30); above the map. (Was z=50, which painted OVER
              // the chat + layers  -  the reported bug.)
              zIndex: LEGEND_Z_INDEX,
            }}
          >
            {/* LEGEND v2 - ROW 1: title + hide(eye). Always shown (flat key). */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 5,
                gap: 6,
              }}
            >
              <span
                data-testid="layer-legend-title"
                style={{
                  fontSize: titleFont,
                  fontWeight: 600,
                  letterSpacing: "0.03em",
                  color: "#ddd",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {preset.label}
              </span>
              <LegendControls idx={idx} onHide={() => setHidden(true)} />
            </div>

            {/* LEGEND v2 - ROW 2: the gradient bar flanked by the min/max values.
                HORIZONTAL (top/bottom dock): [min] bar [max] in a row, min at the
                LEFT end of the bar, max at the RIGHT. VERTICAL (left/right dock):
                the same rotates - max at the TOP, min at the BOTTOM of a tall
                vertical bar. The bar grows to fill so the values flank its ends. */}
            {orientation === "vertical" ? (
              <div
                data-testid="layer-legend-value-row"
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 4,
                  marginTop: 2,
                }}
              >
                <span
                  data-testid="layer-legend-max-label"
                  style={{ fontSize: labelFont, color: "#bbb" }}
                >
                  {maxText}
                </span>
                <div
                  data-testid="layer-legend-bar"
                  style={{
                    width: barThickness,
                    height: verticalBarHeight,
                    borderRadius: 3,
                    background: gradient,
                    border: "1px solid rgba(255,255,255,0.12)",
                    flexShrink: 0,
                  }}
                />
                <span
                  data-testid="layer-legend-min-label"
                  style={{ fontSize: labelFont, color: "#bbb" }}
                >
                  {minText}
                </span>
              </div>
            ) : (
              <div
                data-testid="layer-legend-value-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  marginTop: 2,
                }}
              >
                <span
                  data-testid="layer-legend-min-label"
                  style={{
                    fontSize: labelFont,
                    color: "#bbb",
                    flexShrink: 0,
                    whiteSpace: "nowrap",
                  }}
                >
                  {minText}
                </span>
                <div
                  data-testid="layer-legend-bar"
                  style={{
                    flex: 1,
                    minWidth: 0,
                    height: barThickness,
                    borderRadius: 3,
                    background: gradient,
                    border: "1px solid rgba(255,255,255,0.12)",
                  }}
                />
                <span
                  data-testid="layer-legend-max-label"
                  style={{
                    fontSize: labelFont,
                    color: "#bbb",
                    flexShrink: 0,
                    whiteSpace: "nowrap",
                  }}
                >
                  {maxText}
                </span>
              </div>
            )}

            {/* Resize handle (bottom-right corner). LEGEND v2: a plain hit-target
                (no diagonal grip glyph - NATE asked to drop the visual clutter);
                the card body itself is the drag handle. */}
            <div
              data-legend-no-drag=""
              data-testid="layer-legend-resize"
              onPointerDown={(e) => startResize(layerId, e)}
              style={{
                position: "absolute",
                right: 2,
                bottom: 2,
                width: 12,
                height: 12,
                cursor: "ew-resize",
                borderBottomRightRadius: 8,
              }}
              aria-label="Resize legend key"
            />
          </div>
        );

        // Portal each key card to document.body so it escapes the map
        // container's stacking context and renders above the mobile drawer.
        return createPortal(keyCard, document.body, `legend-key-${layerId}`);
      })}

      {/* LEGEND v2 (DROP-ZONE SIGNALS) - while a key is being dragged, paint a
          thin "area signal" along each VALID snap target (left/right/top edges of
          the AOI; never bottom) and HIGHLIGHT the one nearest the dragged card.
          Portaled to document.body (position:fixed) like the keys so they overlay
          the map at the AOI edges. Cleared automatically on release/cancel. */}
      {dropSignals.map((sig) =>
        createPortal(
          <div
            key={`legend-dropzone-${sig.side}`}
            data-testid="layer-legend-dropzone"
            data-legend-dropzone-side={sig.side}
            data-legend-dropzone-active={sig.active ? "1" : "0"}
            aria-hidden="true"
            style={{
              position: "fixed",
              left: sig.rect.left,
              top: sig.rect.top,
              width: Math.max(0, sig.rect.right - sig.rect.left),
              height: Math.max(0, sig.rect.bottom - sig.rect.top),
              borderRadius: 3,
              background: sig.active
                ? "rgba(74,163,255,0.85)"
                : "rgba(74,163,255,0.28)",
              boxShadow: sig.active ? "0 0 8px rgba(74,163,255,0.7)" : "none",
              pointerEvents: "none",
              transition: "background 80ms linear",
              zIndex: LEGEND_Z_INDEX,
            }}
          />,
          document.body,
          `legend-dropzone-${sig.side}`,
        ),
      )}
    </div>
  );
}

/** LEGEND v2 - per-key control cluster: just the HIDE (eye) button. The compact
 * collapse/expand toggle is GONE (the key is always a flat two-row card). Only
 * the FIRST key carries the global hide control, to avoid clutter. Tagged
 * `data-legend-no-drag` so a click on it does not initiate a card drag. */
function LegendControls({
  idx,
  onHide,
}: {
  idx: number;
  onHide: () => void;
}): JSX.Element | null {
  // Only the first key carries the hide control; the rest render no controls.
  if (idx !== 0) return null;
  return (
    <span
      data-legend-no-drag=""
      style={{ display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0 }}
    >
      <button
        type="button"
        data-testid="layer-legend-hide"
        data-legend-no-drag=""
        onClick={onHide}
        title="Hide legend"
        aria-label="Hide legend"
        style={controlBtnStyle}
      >
        {/* An eye glyph for the hide affordance (NATE's "hide(eye) button"). */}
        <span aria-hidden="true" style={{ fontSize: 11, lineHeight: "14px" }}>
          {EYE_GLYPH}
        </span>
      </button>
    </span>
  );
}

// A minimal eye glyph for the hide control (kept as a constant so the ASCII
// source stays clean). U+1F441 = eye.
const EYE_GLYPH = "\u{1F441}";

const controlBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 18,
  height: 16,
  lineHeight: "14px",
  padding: 0,
  fontSize: 11,
  fontWeight: 700,
  color: "#bbb",
  background: "rgba(255,255,255,0.06)",
  border: "1px solid rgba(255,255,255,0.10)",
  borderRadius: 4,
  cursor: "pointer",
};

/**
 * Item b (NATE 2026-06-20)  -  the MOBILE legend show/hide control, rendered
 * INSIDE the expanded Layers section (the LayerPanel) instead of floating over
 * the chat composer. It is a plain inline row (no portal), so it sits in the
 * panel's normal flow, out of the way. The legend's own floating pill is
 * suppressed on mobile (`suppressShowPill`), so this is the ONLY show/hide
 * affordance there.
 *
 * Pure controlled component: the parent owns the `hidden` boolean (App threads
 * the same value into LayerLegend's `hidden` prop). Render it only when there is
 * legend content to toggle (`legendHasContent(layers)`).
 */
export function MobileLegendToggle({
  hidden,
  onToggle,
}: {
  hidden: boolean;
  onToggle: (hidden: boolean) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      data-testid="grace2-mobile-legend-toggle"
      aria-pressed={!hidden}
      onClick={() => onToggle(!hidden)}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
        width: "100%",
        padding: "8px 10px",
        background: "rgba(255,255,255,0.04)",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 8,
        color: "#cfd4db",
        fontFamily: "system-ui, sans-serif",
        fontSize: 12,
        fontWeight: 600,
        cursor: "pointer",
      }}
    >
      <span>{hidden ? "Show legend" : "Hide legend"}</span>
      <span
        aria-hidden="true"
        style={{
          fontSize: 11,
          color: hidden ? "#8a929e" : "#4aa3ff",
          fontWeight: 700,
        }}
      >
        {hidden ? "OFF" : "ON"}
      </span>
    </button>
  );
}
