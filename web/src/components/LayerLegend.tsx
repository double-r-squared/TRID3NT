// GRACE-2 web — LayerLegend (job-0065).
//
// Renders a matplotlib-style horizontal colorbar for the topmost
// continuous-raster layer that has a known style_preset.
//
// Positioning:
//   The component is absolute-positioned at the bottom-center of whichever
//   container it is placed in. It is rendered INSIDE the map container div
//   in App.tsx (the flex child that holds MapView), so that when side panels
//   collapse the map container grows and the legend stays centered over it
//   automatically — no JS measurement needed.
//
// Data flow:
//   LayerLegend accepts the current ordered layer list (top-of-stack first)
//   directly as a prop. App.tsx passes the same list the LayerPanel already
//   maintains. The component picks the topmost layer whose layer_type is
//   "raster" and whose style_preset is registered in STYLE_PRESETS; it hides
//   (returns null) if no such layer is found.
//
// Invariant 1: this component displays received values only — no computation.
//   The minValue / maxValue / stops come from the preset registry which
//   mirrors the QML; the layer name comes from the ProjectLayerSummary wire.

import { ProjectLayerSummary } from "../contracts";
import { getStylePreset, StylePreset } from "../lib/style-presets";

export interface LayerLegendProps {
  /** Ordered layer list, top-of-stack first (same order as LayerPanel). */
  layers: ProjectLayerSummary[];
  /**
   * job-0321 (F43) — optional screen-space anchor. When provided, the legend
   * positions itself at this {left, top} (absolute, with a translateX(-50%) so
   * `left` is the CENTER x), so it can hang off the bottom edge of the AOI
   * bounding box on the map and read as the depth-key for that AOI. The owner
   * (Map.tsx) projects the bbox bottom-edge midpoint each move/zoom/render.
   *
   * When `anchor` is null/undefined the legend keeps its previous bottom-center
   * placement (AOI-less Cases, or the bbox is off-screen) so it never vanishes.
   */
  anchor?: { left: number; top: number } | null;
}

/**
 * Builds the CSS linear-gradient string from the preset's stops.
 * Stops are already sorted by position ascending.
 */
function buildGradient(preset: StylePreset): string {
  const parts = preset.stops
    .map((s) => `${s.color} ${(s.position * 100).toFixed(2)}%`)
    .join(", ");
  return `linear-gradient(to right, ${parts})`;
}

export function LayerLegend({ layers, anchor }: LayerLegendProps): JSX.Element | null {
  // Find the topmost continuous-raster layer with a known preset.
  // "topmost" = first in the top-of-stack-first list (highest z_index).
  const targetLayer = layers.find(
    (l) =>
      l.layer_type === "raster" &&
      l.style_preset != null &&
      getStylePreset(l.style_preset) != null,
  );

  if (!targetLayer || !targetLayer.style_preset) return null;

  const preset = getStylePreset(targetLayer.style_preset);
  if (!preset) return null;

  const gradient = buildGradient(preset);

  // job-0321 (F43) — anchored vs fallback placement. When an `anchor` is given
  // (Map.tsx projected the AOI bbox bottom-edge midpoint), position the legend
  // at that screen point with translateX(-50%) so it hangs centered under the
  // box's bottom edge. Otherwise keep the original bottom-center placement so
  // AOI-less Cases (or an off-screen bbox) never lose the legend.
  const placement: React.CSSProperties = anchor
    ? {
        left: anchor.left,
        top: anchor.top,
        transform: "translateX(-50%)",
      }
    : {
        bottom: 24,
        left: "50%",
        transform: "translateX(-50%)",
      };

  return (
    <div
      data-testid="grace2-layer-legend"
      style={{
        position: "absolute",
        ...placement,
        width: 320,
        padding: "8px 12px 10px",
        // job-0283 — joins the panel surface family: hairline border + 10px
        // radius + 6px blur (was a border-less 8px/4px card). Form-factor
        // shared by design — the legend is not a job-0280 drawer/sheet
        // surface, so the family alignment applies on mobile too.
        background: "rgba(17,18,23,0.78)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 10,
        boxShadow: "0 2px 12px rgba(0,0,0,0.45)",
        fontFamily: "system-ui, sans-serif",
        color: "#eee",
        pointerEvents: "none", // let map interactions pass through
        zIndex: 10,
      }}
    >
      {/* Title */}
      <div
        data-testid="layer-legend-title"
        style={{
          fontSize: 11,
          fontWeight: 600,
          textAlign: "center",
          marginBottom: 5,
          letterSpacing: "0.03em",
          color: "#ddd",
        }}
      >
        {preset.label}
      </div>

      {/* Gradient bar */}
      <div
        data-testid="layer-legend-bar"
        style={{
          height: 14,
          borderRadius: 3,
          background: gradient,
          border: "1px solid rgba(255,255,255,0.12)",
        }}
      />

      {/* Axis labels */}
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
    </div>
  );
}
