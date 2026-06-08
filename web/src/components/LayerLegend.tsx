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

export function LayerLegend({ layers }: LayerLegendProps): JSX.Element | null {
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

  return (
    <div
      data-testid="grace2-layer-legend"
      style={{
        position: "absolute",
        bottom: 24,
        left: "50%",
        transform: "translateX(-50%)",
        width: 320,
        padding: "8px 12px 10px",
        background: "rgba(15,15,20,0.72)",
        backdropFilter: "blur(4px)",
        WebkitBackdropFilter: "blur(4px)",
        borderRadius: 8,
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
