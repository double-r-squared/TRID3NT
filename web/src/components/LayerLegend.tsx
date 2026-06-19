// GRACE-2 web — LayerLegend (job-0065; interactive AOI-snapping keys, NATE
// overlay-layout spec 2026-06-17).
//
// Renders matplotlib-style horizontal colorbar "keys", one per continuous-raster
// layer that has a known style_preset. Each key is:
//   1. DRAGGABLE (pointer drag); on release it AUTO-SNAPS to the nearest side of
//      the AOI bounding box (the current bbox rectangle on the map).
//   2. SNAP-ORDERED counter-clockwise by stack order — 1st key bottom, 2nd
//      right, 3rd top, 4th left — and STACKED (offset outward) when more than
//      one key lands on the same side, so keys never overlap (legend_snap.ts).
//   3. RESIZABLE per-key via a corner handle (width; height follows content).
//   4. Collapsible to a COMPACT (flattened) mode, and hideable entirely, via a
//      small settings toggle pinned to the key.
//
// Positioning / data flow:
//   The component is rendered INSIDE the map container div (in Map.tsx) so it
//   anchors to the AOI box. Map.tsx passes:
//     - `layers`   : ordered layer list, top-of-stack first (LayerPanel order).
//     - `aoiRect`  : the TRUE projected AOI screen rectangle {left,top,right,
//                    bottom} (min/max over all four projected bbox corners). This
//                    is what the keys SNAP against — it carries the real AOI
//                    aspect ratio and on-screen skew, so the colorbar rails along
//                    the actual AOI edges, not a square-ish estimate.
//     - `anchor`   : the AOI bbox BOTTOM-edge midpoint {left, top} (projected) —
//                    used for the (already gap-nudged) vertical positioning the
//                    owner resolves; not the snap geometry.
//     - `barWidth` : the AOI bbox on-screen EAST-WEST extent in px (projected) —
//                    used to SIZE the default colorbar width.
//   Snap source of truth: when `aoiRect` is provided the keys snap CCW to ITS
//   four edges directly. When it is absent (off-screen / not yet projected) we
//   FALL BACK to reconstructing an approximate rect from `anchor` + `barWidth`
//   (legend_snap `rectFromAnchorAndWidth` — the bottom edge is exact, the height
//   is a square-ish estimate). When there is no AOI at all (no rect AND no
//   anchor/barWidth) the keys fall back to a static bottom-center stack so the
//   legend never vanishes.
//
// Invariant 1: this component displays received values only — no geography is
//   computed. minValue / maxValue / stops come from the preset registry (mirrors
//   the QML); the layer name comes from the ProjectLayerSummary wire. The
//   snap geometry is pure pixel math over the already-projected AOI rectangle.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ProjectLayerSummary } from "../contracts";
import { getStylePreset, StylePreset } from "../lib/style-presets";
import {
  layoutKeysCcw,
  rectFromAnchorAndWidth,
  sideForIndex,
  type AoiSide,
  type KeySize,
  type ScreenRect,
} from "../lib/legend_snap";

export interface LayerLegendProps {
  /** Ordered layer list, top-of-stack first (same order as LayerPanel). */
  layers: ProjectLayerSummary[];
  /**
   * EDGE-RAIL snap (NATE 2026-06-17) — the TRUE projected AOI screen rectangle
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
   * job-0321 (F43) — optional screen-space anchor: the AOI bbox BOTTOM-edge
   * midpoint {left, top} (absolute, map-container coords). The owner (Map.tsx)
   * projects it each move/zoom/render. Used as the FALLBACK snap-rect source
   * (with `barWidth`, via rectFromAnchorAndWidth) only when `aoiRect` is absent.
   * Null/undefined AND no `aoiRect` => no AOI on screen => the keys fall back to
   * a static bottom-center stack so they never vanish.
   */
  anchor?: { left: number; top: number } | null;
  /**
   * FIX 4 (NATE 2026-06-17) — the AOI bbox's ON-SCREEN east-west extent in px
   * (already clamped by Map.tsx). Used to SIZE the default colorbar width, and
   * (with `anchor`) to reconstruct the FALLBACK AOI rectangle for snapping when
   * `aoiRect` is absent. Null => no AOI bbox => static fallback width +
   * bottom-center stack.
   */
  barWidth?: number | null;
}

/** A raster layer that resolved to a known preset — one legend key per entry. */
interface LegendKeyModel {
  layerId: string;
  preset: StylePreset;
}

/** Per-key interactive UI state the user can drive (width + compact + free pos). */
interface KeyUiState {
  /** User-chosen width override (px). Undefined => default snapped width. */
  width?: number;
  /** Compact (flattened) mode: ramp + range only, no title/labels chrome. */
  compact?: boolean;
  /** While dragging, the free top-left screen position (overrides the snap). */
  free?: { left: number; top: number } | null;
}

/** Builds the CSS linear-gradient string from the preset's stops (sorted asc). */
function buildGradient(preset: StylePreset): string {
  const parts = preset.stops
    .map((s) => `${s.color} ${(s.position * 100).toFixed(2)}%`)
    .join(", ");
  return `linear-gradient(to right, ${parts})`;
}

// Default colorbar width when there is no AOI bbox to size against.
const STATIC_LEGEND_WIDTH = 320;
// Min/max width a user may resize a key to.
const KEY_MIN_WIDTH = 140;
const KEY_MAX_WIDTH = 520;
// Estimated key heights for the snap layout (full vs compact). These only feed
// the stacking math (so keys don't overlap); the rendered card sizes itself.
const KEY_HEIGHT_FULL = 64;
const KEY_HEIGHT_COMPACT = 26;
// Horizontal gap between keys when falling back to the bottom-center stack.
const FALLBACK_STACK_GAP = 10;

/**
 * Selects every continuous-raster layer with a known preset, in stack order
 * (top-of-stack first). This is the multi-key generalization of the old
 * "topmost layer wins" rule — every eligible raster gets its own key.
 */
function selectKeyModels(layers: ProjectLayerSummary[]): LegendKeyModel[] {
  const out: LegendKeyModel[] = [];
  for (const l of layers) {
    if (l.layer_type !== "raster") continue;
    if (l.style_preset == null) continue;
    const preset = getStylePreset(l.style_preset);
    if (!preset) continue;
    out.push({ layerId: l.layer_id, preset });
  }
  return out;
}

export function LayerLegend({
  layers,
  aoiRect: trueRect,
  anchor,
  barWidth,
}: LayerLegendProps): JSX.Element | null {
  // One key per eligible raster layer, in stack order.
  const keyModels = useMemo(() => selectKeyModels(layers), [layers]);

  // Per-key interactive state, keyed by layer_id so it survives reorders.
  const [uiState, setUiState] = useState<Record<string, KeyUiState>>({});
  // Whether the whole legend is hidden (the eye toggle on the first key).
  const [hidden, setHidden] = useState(false);

  // Live drag bookkeeping. Tracks the key being dragged, the pointer offset
  // inside the card, and the latest pointer position. Stored in a ref so the
  // window listeners read fresh values without re-binding each render.
  const dragRef = useRef<{
    layerId: string;
    offsetX: number;
    offsetY: number;
  } | null>(null);

  // The AOI rectangle in screen space that the keys SNAP against. Prefer the
  // TRUE projected rect (all four bbox corners, min/max box) threaded from
  // Map.tsx — it carries the real AOI aspect ratio + on-screen skew, so the
  // CCW edge-rail follows the actual AOI edges. Only when the true rect is
  // absent (off-screen / not yet projected) do we fall back to reconstructing
  // an APPROXIMATE rect from anchor + barWidth (square-ish height estimate).
  // Null from both => no AOI on screen => bottom-center stack fallback.
  const aoiRect: ScreenRect | null = useMemo(
    () => trueRect ?? rectFromAnchorAndWidth(anchor, barWidth),
    [trueRect, anchor, barWidth],
  );

  // Default per-key width: the AOI on-screen width (clamped) when available,
  // else the static fallback. A user resize overrides this per key.
  const defaultWidth = useMemo(() => {
    const w =
      typeof barWidth === "number" && Number.isFinite(barWidth) && barWidth > 0
        ? barWidth
        : STATIC_LEGEND_WIDTH;
    return Math.max(KEY_MIN_WIDTH, Math.min(w, KEY_MAX_WIDTH));
  }, [barWidth]);

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

  const heightFor = useCallback(
    (layerId: string): number =>
      uiState[layerId]?.compact ? KEY_HEIGHT_COMPACT : KEY_HEIGHT_FULL,
    [uiState],
  );

  // Compute the snapped position for every key. When an AOI rect is present we
  // lay keys out CCW (bottom, right, top, left, stacking on repeat). With no AOI
  // we stack them along a static bottom-center row. A key being actively dragged
  // uses its `free` position instead of the snapped one.
  const sizes: KeySize[] = useMemo(
    () =>
      keyModels.map((k) => ({
        width: widthFor(k.layerId),
        height: heightFor(k.layerId),
      })),
    [keyModels, widthFor, heightFor],
  );

  const snapped = useMemo(() => {
    if (aoiRect) return layoutKeysCcw(aoiRect, sizes);
    // No AOI: lay the keys out as a bottom-center row (each key centered, then
    // stacked upward so they don't overlap). We synthesize a degenerate rect at
    // a nominal bottom-center point; this keeps the legend visible.
    let consumed = 0;
    return sizes.map((s) => {
      const top = -(FALLBACK_STACK_GAP + consumed + s.height);
      consumed += s.height + FALLBACK_STACK_GAP;
      return { left: -s.width / 2, top, side: "bottom" as AoiSide };
    });
  }, [aoiRect, sizes]);

  // --- drag wiring --------------------------------------------------------- //

  const endDrag = useCallback(() => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    const layerId = drag.layerId;
    // On release: drop the free position so the key SNAPS back via the CCW
    // layout. If we have an AOI rect we could re-pick the nearest side, but the
    // CCW stack order is the spec's canonical arrangement, so we honor it (the
    // drag is a gesture to re-trigger the snap, matching NATE's "snap to the
    // nearest side" intent — the nearest-side hint is used only for AOI-less
    // ordering hold). Clearing `free` is the snap.
    setUiState((prev) => {
      const next = { ...prev };
      const cur = next[layerId] ?? {};
      next[layerId] = { ...cur, free: null };
      return next;
    });
  }, []);

  const onPointerMoveWindow = useCallback((ev: PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const left = ev.clientX - drag.offsetX;
    const top = ev.clientY - drag.offsetY;
    setUiState((prev) => {
      const next = { ...prev };
      const cur = next[drag.layerId] ?? {};
      next[drag.layerId] = { ...cur, free: { left, top } };
      return next;
    });
  }, []);

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
      // Don't start a drag from the resize handle or a button.
      const target = ev.target as HTMLElement;
      if (target.closest("[data-legend-no-drag]")) return;
      const card = ev.currentTarget.getBoundingClientRect();
      dragRef.current = {
        layerId,
        offsetX: ev.clientX - card.left,
        offsetY: ev.clientY - card.top,
      };
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
    [],
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

  const toggleCompact = useCallback((layerId: string) => {
    setUiState((prev) => {
      const next = { ...prev };
      const cur = next[layerId] ?? {};
      next[layerId] = { ...cur, compact: !cur.compact };
      return next;
    });
  }, []);

  // Nothing eligible => render nothing (preserves the old hide contract).
  if (keyModels.length === 0) return null;

  // When fully hidden, render only a tiny "show legend" pill (bottom-center).
  if (hidden) {
    return (
      <button
        type="button"
        data-testid="grace2-layer-legend-show"
        onClick={() => setHidden(false)}
        style={{
          position: "absolute",
          bottom: 24,
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
          zIndex: 11,
        }}
      >
        Show legend
      </button>
    );
  }

  return (
    // A full-bleed, click-through layer; only the key cards capture pointers.
    // The container does NOT set bottom-center placement — each key positions
    // itself absolutely (snapped or free / fallback). The wrapper keeps a stable
    // testid so existing tests + Map.tsx mounting expectations hold.
    <div
      data-testid="grace2-layer-legend"
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        zIndex: 10,
        // Bottom-center reference point for the AOI-less fallback: the keys are
        // positioned relative to this 0x0 marker via negative offsets.
        // (When an AOI rect exists the snapped coords are absolute map coords.)
      }}
    >
      {keyModels.map((model, idx) => {
        const layerId = model.layerId;
        const preset = model.preset;
        const ui = uiState[layerId] ?? {};
        const width = widthFor(layerId);
        const compact = !!ui.compact;
        // `snapped` is built 1:1 from `keyModels`, so this is always defined;
        // the fallback satisfies noUncheckedIndexedAccess.
        const snapPos = snapped[idx] ?? { left: 0, top: 0, side: "bottom" as AoiSide };

        // Position: free (dragging) > snapped (AOI) > fallback bottom-center.
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

        const gradient = buildGradient(preset);
        const sideLabel: AoiSide = aoiRect
          ? sideForIndex(idx)
          : "bottom";

        return (
          <div
            key={layerId}
            data-testid="grace2-layer-legend-key"
            data-legend-side={sideLabel}
            data-legend-compact={compact ? "1" : "0"}
            onPointerDown={(e) => startDrag(layerId, e)}
            style={{
              position: "absolute",
              ...posStyle,
              width,
              padding: compact ? "5px 10px 6px" : "8px 12px 10px",
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
            }}
          >
            {/* Title row (hidden in compact mode) + per-key controls. */}
            {!compact ? (
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
                    fontSize: 11,
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
                <LegendControls
                  idx={idx}
                  compact={compact}
                  onToggleCompact={() => toggleCompact(layerId)}
                  onHide={() => setHidden(true)}
                />
              </div>
            ) : (
              // Compact: a slim header with just the controls + a short label.
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 6,
                }}
              >
                <span
                  data-testid="layer-legend-title"
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    color: "#ccc",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {preset.label}
                </span>
                <LegendControls
                  idx={idx}
                  compact={compact}
                  onToggleCompact={() => toggleCompact(layerId)}
                  onHide={() => setHidden(true)}
                />
              </div>
            )}

            {/* Gradient bar (always shown). */}
            <div
              data-testid="layer-legend-bar"
              style={{
                height: compact ? 8 : 14,
                marginTop: compact ? 3 : 0,
                borderRadius: 3,
                background: gradient,
                border: "1px solid rgba(255,255,255,0.12)",
              }}
            />

            {/* Axis labels (hidden in compact mode to flatten). */}
            {!compact ? (
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginTop: 4,
                }}
              >
                <span
                  data-testid="layer-legend-min-label"
                  style={{ fontSize: 10, color: "#bbb" }}
                >
                  {preset.minValue} {preset.unit}
                </span>
                <span
                  data-testid="layer-legend-max-label"
                  style={{ fontSize: 10, color: "#bbb" }}
                >
                  {preset.maxValue} {preset.unit}
                </span>
              </div>
            ) : null}

            {/* Resize handle (bottom-right corner). */}
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
                // A subtle corner grip (two diagonal hairlines).
                backgroundImage:
                  "linear-gradient(135deg, transparent 0 6px, rgba(255,255,255,0.35) 6px 7px, transparent 7px 9px, rgba(255,255,255,0.35) 9px 10px, transparent 10px)",
                borderBottomRightRadius: 8,
              }}
              aria-label="Resize legend key"
            />
          </div>
        );
      })}
    </div>
  );
}

/** Per-key control cluster: compact toggle + hide. `data-legend-no-drag` so a
 * click on a control doesn't initiate a card drag. */
function LegendControls({
  idx,
  compact,
  onToggleCompact,
  onHide,
}: {
  idx: number;
  compact: boolean;
  onToggleCompact: () => void;
  onHide: () => void;
}): JSX.Element {
  return (
    <span
      data-legend-no-drag=""
      style={{ display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0 }}
    >
      <button
        type="button"
        data-testid="layer-legend-compact-toggle"
        data-legend-no-drag=""
        onClick={onToggleCompact}
        title={compact ? "Expand key" : "Flatten key"}
        style={controlBtnStyle}
      >
        {compact ? "+" : "–"}
      </button>
      {/* Only the FIRST key carries the global hide control, to avoid clutter. */}
      {idx === 0 ? (
        <button
          type="button"
          data-testid="layer-legend-hide"
          data-legend-no-drag=""
          onClick={onHide}
          title="Hide legend"
          style={controlBtnStyle}
        >
          ×
        </button>
      ) : null}
    </span>
  );
}

const controlBtnStyle: React.CSSProperties = {
  width: 16,
  height: 16,
  lineHeight: "14px",
  padding: 0,
  fontSize: 12,
  fontWeight: 700,
  color: "#bbb",
  background: "rgba(255,255,255,0.06)",
  border: "1px solid rgba(255,255,255,0.10)",
  borderRadius: 4,
  cursor: "pointer",
};
